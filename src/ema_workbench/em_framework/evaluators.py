'''


'''
from __future__ import (unicode_literals, print_function, absolute_import,
                                        division)

import multiprocessing
import numbers 
import os
import threading

from .callbacks import DefaultCallback
from .ema_multiprocessing import LogQueueReader, initializer, add_tasks
from .ema_ipyparallel import (start_logwatcher, set_engine_logger, 
                              initialize_engines, cleanup, _run_experiment)
from .experiment_runner import ExperimentRunner
from .model import AbstractModel
from .outcomes import AbstractOutcome
from .parameters import experiment_generator, Scenario, Policy
from .samplers import (MonteCarloSampler, FullFactorialSampler, LHSSampler, 
                       PartialFactorialSampler, sample_levers, 
                       sample_uncertainties)
from .salib_samplers import (SobolSampler, MorrisSampler, FASTSampler) # TODO:: should become optional import
from .util import NamedObjectMap, determine_objects
from ..util import ema_logging, EMAError


# Created on 5 Mar 2017
#
# .. codeauthor::jhkwakkel <j.h.kwakkel (at) tudelft (dot) nl>

LHS = 'lhs'
MC = 'mc'
FF = 'ff'
PFF = 'pff'
SOBOL = 'sobol'
MORRIS = 'morris'
FAST = 'fast'

#TODO:: better name, samplers lower case conflicts with module name
SAMPLERS = {LHS:LHSSampler,
            MC:MonteCarloSampler,
            FF:FullFactorialSampler,
            PFF:PartialFactorialSampler,
            SOBOL:SobolSampler,
            MORRIS:MorrisSampler,
            FAST:FASTSampler}

__all__ = ['MultiprocessingPoolEvaluator', 'IpyparallelEvaluator']

class BaseEvaluator(object):
    '''evaluator for experiments using a multiprocessing pool
    
    Parameters
    ----------
    msis : collection of models
    searchover : {None, 'levers', 'uncertainties'}, optional
                  to be used in combination with platypus
    union : {None, True, False}, optional
            to be used in combination with platypus, indicates whether
            you want to optimize over the union or the intersection of
            search_over
    
    Raises
    ------
    ValueError
    
    '''
    
    def __init__(self, msis, searchover=None, union=None):
        super(BaseEvaluator, self).__init__()
        
        if isinstance(msis, AbstractModel):
            msis = [msis]
        
        self._msis = msis
        
        if searchover:
            if searchover not in {'levers', 'uncertainties'}:
                raise ValueError(("search_over must be one of 'levers'"
                              "or 'uncertainties' not {}".format(searchover)))
            
            self.searchover = searchover
            
            self.parameters = determine_objects(msis, searchover, union=union)
            self.parameter_names = [p.name for p in self.parameters]
            
            outcomes = determine_objects(msis, "outcomes", union=union)
            self.outcomes = [o for o in outcomes if
                             o.kind != AbstractOutcome.INFO]
            self.outcome_names = [o.name for o in self.outcomes]

    def __enter__(self):
        return self


    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            return False

        
    def evaluate_experiments(self, scenarios, policies, callback):
        '''used by ema_workbench'''
        raise NotImplementedError

    
    def evaluate_all(self, jobs, **kwargs):
        '''make ema_workbench evaluators compatible with Platypus'''
        policies = []
        scenarios = []
        
        for i, job in enumerate(jobs):
            variables = dict(zip(self.parameter_names, job.solution.variables))
            
                # we can now evaluate the model
            if self.searchover=='levers':
                job = Policy(name=str(i), **variables)
                policies.append(job)
            else:
                job = Scenario(**variables)
                scenarios.append(job)
        
        if not policies:
            policies = 0
        if not scenarios:
            scenarios = 0
        
        experiments, outcomes = perform_experiments(self._msis, 
                                        scenarios=scenarios, policies=policies, 
                                        evaluator=self)
                
        # map back cases to jobs
        # TODO:: not correct for scenarios, we probably need to 
        # include a scenario_id in the experiments, just like we have a 
        # policy_id
        for i, job_id in enumerate(experiments['policy']):
            job_outcomes = [outcomes[o][i] for o in self.outcome_names]
            job = jobs[int(job_id)]
            
            job.solution.problem.function = lambda x: job_outcomes
            job.solution.evaluate()
            
        return jobs


class SequentialEvaluator(BaseEvaluator):
    def __init__(self, models, **kwargs):
        super(SequentialEvaluator, self).__init__(models, **kwargs)
        
    def evaluate_experiments(self, scenarios, policies, callback):
        ema_logging.info("performing experiments sequentially")
        
        ex_gen = experiment_generator(scenarios, self._msis, policies)
        
        models = NamedObjectMap(AbstractModel)
        models.extend(self._msis)
        
        cwd = os.getcwd() 
        runner = ExperimentRunner(models)
        for experiment in ex_gen:
            result = runner.run_experiment(experiment)
            callback(experiment, result)
        runner.cleanup()
        os.chdir(cwd)
    

class MultiprocessingPoolEvaluator(BaseEvaluator):
    '''evaluator for experiments using a multiprocessing pool
    
    Parameters
    ----------
    msis : collection of models
    n_processes : int (optional)
    
    
    '''
    
    def __init__(self, msis, n_processes=None, **kwargs):
        super(MultiprocessingPoolEvaluator, self).__init__(msis, **kwargs)
        
        self._pool = None
        self.n_processes = n_processes

    def __enter__(self):
        log_queue = multiprocessing.Queue()
    
        log_queue_reader = LogQueueReader(log_queue)
        log_queue_reader.start()
    
        try:
            loglevel = ema_logging._logger.getEffectiveLevel()
        except AttributeError:
            loglevel=30
    
        self._pool = multiprocessing.Pool(self.n_processes , initializer, 
                                          (self._msis, log_queue, loglevel))
        ema_logging.info("pool started")
        return self


    def __exit__(self, exc_type, exc_value, traceback):
        ema_logging.info("terminating pool")
        
        if exc_type is not None:
            # When an exception is thrown stop accepting new jobs
            # and abort pending jobs without waiting.
            self._pool.terminate()
            return False
        
        # Stop accepting new jobs and wait for pending jobs to finish.
        self._pool.close()
        self._pool.join()
        
    def evaluate_experiments(self, scenarios, policies, callback):
        ex_gen = experiment_generator(scenarios, self._msis, policies)
        
        add_tasks(self._pool, ex_gen, callback)


class IpyparallelEvaluator(BaseEvaluator):

    def __init__(self,  msis, client, **kwargs):
        super(IpyparallelEvaluator, self).__init__(msis, **kwargs)
        self.client = client
        
    def __enter__(self):
        import ipyparallel
        
        ema_logging.debug("starting ipyparallel pool")

        try:
            TIMEOUT_MAX = threading.TIMEOUT_MAX
        except AttributeError:
            TIMEOUT_MAX = 1e10  # noqa        
        ipyparallel.client.asyncresult._FOREVER = TIMEOUT_MAX
        # update loggers on all engines
        self.client[:].apply_sync(set_engine_logger)
        
        ema_logging.debug("initializing engines")
        initialize_engines(self.client, self._msis, 
                                                os.getcwd())
        
        self.logwatcher, self.logwatcher_thread = start_logwatcher()
        
        ema_logging.debug("successfully started ipyparallel pool")
        
        ema_logging.info("performing experiments using ipyparallel")
        
        return self


    def __exit__(self, exc_type, exc_value, traceback):
        self.logwatcher.stop()
        cleanup(self.client)
        
        
    def evaluate_experiments(self, scenarios, policies, callback):
        ex_gen = experiment_generator(scenarios, self._msis, policies)
        
        lb_view = self.client.load_balanced_view()
        
        results = lb_view.map(_run_experiment, 
                              ex_gen, ordered=False, block=False)

        for entry in results:
            callback(*entry)
        


def perform_experiments(models, scenarios=0, policies=0, evaluator=None, 
                        reporting_interval=None, uncertainty_union=False, 
                        lever_union=False, outcome_union=False, 
                        uncertainty_sampling=LHS, levers_sampling=LHS):
    '''sample uncertainties and levers, and perform the resulting experiments
    on each of the models
    
    Parameters
    ----------
    models : one or more AbstractModel instances
    scenarios : int or collection of Scenario instances, optional
    policies :  int or collection of Policy instances, optional
    evaluator : Evaluator instance, optional
    reporting interval : int, optional
    uncertainty_union : boolean, optional
    lever_union : boolean, optional
    uncertainty_sampling : {LHS, MC, FF, PFF, SOBOL, MORRIS, FAST}, optional
    lever_sampling : {LHS, MC, FF, PFF, SOBOL, MORRIS, FAST}, optional
    
    
    '''
    if not scenarios and not policies:
        raise EMAError(('no experiments possible since both ' 
                        'scenarios and policies are 0'))
    
    if not scenarios:
        scenarios = [Scenario("None", **{})]
        uncertainties = []
        n_scenarios = 1
    elif(isinstance(scenarios, numbers.Integral)):
        scenarios = sample_uncertainties(models, scenarios, 
             union=uncertainty_union, sampler=SAMPLERS[uncertainty_sampling]())
        uncertainties = scenarios.parameters
        n_scenarios = scenarios.n
    else:
        uncertainties = determine_objects(models, "uncertainties", union=True)
        uncertainties = [u for u in uncertainties if u.name in scenarios[0]]
        n_scenarios = len(scenarios)
        
    
    if not policies:
        policies = [Policy("None", **{})]
        levers = []
        n_policies = 1
    elif(isinstance(policies, numbers.Integral)):    
        policies = sample_levers(models, policies, union=lever_union, 
                                 sampler=SAMPLERS[levers_sampling]())
        levers = policies.parameters
        n_policies = policies.n
    else:
        levers = determine_objects(models, "levers", union=True)
        levers = [l for l in levers if l.name in policies[0]]
        n_policies = len(policies)
    
    try:
        n_models = len(models)
    except TypeError:
        n_models = 1

    outcomes = determine_objects(models, 'outcomes', union=outcome_union)
    nr_of_exp = n_models * n_scenarios * n_policies 
        
    callback = DefaultCallback(uncertainties,
                               levers,
                               outcomes,
                               nr_of_exp,
                               reporting_interval=reporting_interval)
    
    if not evaluator:
        evaluator = SequentialEvaluator(models)
    
    evaluator.evaluate_experiments(scenarios, policies, callback)
    
    if callback.i != nr_of_exp:
        raise EMAError(('some fatal error has occurred while '
                        'running the experiments, not all runs have ' 
                        'completed. expected {} '.format(nr_of_exp),
                        'got {}'.format(callback.i),
                        '{}'.format(type(callback))))
       
    results = callback.get_results()
    ema_logging.info("experiments finished")
    return results
