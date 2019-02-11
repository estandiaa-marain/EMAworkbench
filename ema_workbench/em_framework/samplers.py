'''

This module contains various classes that can be used for specifying different
types of samplers. These different samplers implement basic sampling
techniques including Full Factorial sampling, Latin Hypercube sampling, and
Monte Carlo sampling.

'''
from __future__ import (absolute_import, print_function, division,
                        unicode_literals)
# try:
#     from future_builtins import zip
# except ImportError:
#     try:
#         from itertools import izip as zip  # < 2.5 or 3.x
#     except ImportError:
#         pass

import abc
import functools
import itertools
import numpy as np
import operator
import scipy.stats as stats

from . import util
from .parameters import (IntegerParameter, Policy, Scenario,
                         BooleanParameter, CategoricalParameter, Category)

# Created on 16 aug. 2011
#
# .. codeauthor:: jhkwakkel <j.h.kwakkel (at) tudelft (dot) nl>

__all__ = ['AbstractSampler',
           'LHSSampler',
           'MonteCarloSampler',
           'FullFactorialSampler',
           'PartialFactorialSampler',
           'sample_levers',
           'sample_uncertainties',
           'determine_parameters']



def _pert(low, peak, high, gamma=4.0):
    """
    A PERT random variate

    Parameters
    ----------
    low : scalar
        Lower bound of the distribution support
    peak : scalar
        The location of the distribution's peak (low <= peak <= high)
    high : scalar
        Upper bound of the distribution support

    Optional
    --------
    gamma : scalar
        Controls the uncertainty of the distribution around the peak. Smaller
        values make the distribution flatter and more uncertain around the
        peak while larger values make it focused and less uncertain around
        the peak. (Default: 4)
    """
    a, b, c = [float(x) for x in [low, peak, high]]
    assert a <= b <= c, ('PERT "peak" must be greater than "low" and '
                         'less than "high"')
    assert gamma >= 0, 'PERT "g" must be non-negative'
    mu = (a + gamma * b + c) / (gamma + 2)
    if mu == b:
        a1 = a2 = 3.0
    else:
        a1 = ((mu - a) * (2 * b - a - c)) / ((b - mu) * (c - a))
        a2 = a1 * (c - mu) / (mu - a)

    return _beta(a1, a2, a, c)


def _pert2(peak, gamma, low, width):
    """
    A PERT random variate with more standardized parameter order,

    This sets the parameters such that the last two are lower bound
    and width, so that it can degenerate cleanly to a uniform.

    Parameters
    ----------
    peak : scalar
        The location of the distribution's peak (low <= peak <= high)
    gamma : scalar
        Controls the uncertainty of the distribution around the peak. Smaller
        values make the distribution flatter and more uncertain around the
        peak while larger values make it focused and less uncertain around
        the peak.
    low : scalar
        Lower bound of the distribution support
    width : scalar
        Distance from lower bound to upper bound of the distribution support

    """
    return _pert(low, peak, low+width, gamma)


def _beta(alpha, beta, low=0, high=1):
    """
    A Beta random variate

    Parameters
    ----------
    alpha : scalar
        The first shape parameter
    beta : scalar
        The second shape parameter

    Optional
    --------
    low : scalar
        Lower bound of the distribution support (default=0)
    high : scalar
        Upper bound of the distribution support (default=1)
    """

    assert alpha > 0 and beta > 0, (
        'Beta "alpha" and "beta" parameters must be greater than zero')
    assert low < high, 'Beta "low" must be less than "high"'

    return stats.beta(alpha, beta, loc=low, scale=high - low)


def _bernoulli(rate, low=0, high=1):
    """
    A PERT random variate with more standardized parameter order,

    This sets the parameters such that the last two are lower bound
    and width, so that it can degenerate cleanly to a uniform.

    Parameters
    ----------
    rate : scalar
        The single parameter of the Bernoulli distribution.
    low : scalar
        Lower bound of the distribution support, assumed 0
    width : scalar
        Distance from lower bound to upper bound of the distribution support,
        assumed 1
    """
    return stats.bernoulli(rate)

class AbstractSampler(object):
    '''
    Abstract base class from which different samplers can be derived.

    In the simplest cases, only the sample method needs to be overwritten.
    generate_designs` is the only method called by the ensemble class. The
    other methods are used internally to generate the designs.

    '''
    __metaaclass__ = abc.ABCMeta

    # types of distributions known by the sampler.
    # by default it knows the `uniform continuous <http://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.uniform.html>`_
    # distribution for sampling floats, and the `uniform discrete <http://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.randint.html#scipy.stats.randint>`_
    # distribution for sampling integers.
    distributions = {
        "uniform": stats.uniform,
        "integer": stats.randint,
        "triangular": stats.triang,
        "triangle": stats.triang,
        "triang": stats.triang,
        "pert": _pert2,
        "bernoulli": _bernoulli,
    }

    # which uniform-type distribution to degrade to for each original distribution
    uniform_distributions = {
        "uniform": stats.uniform,
        "integer": stats.randint,
        "triangular": stats.uniform,
        "triangle": stats.uniform,
        "triang": stats.uniform,
        "pert": stats.uniform,
        "bernoulli": stats.randint,
    }

    def __init__(self):
        super(AbstractSampler, self).__init__()

    @abc.abstractmethod
    def sample(self, distribution, params, size):
        '''
        method for sampling a number of samples from a particular distribution.
        The various samplers differ with respect to their implementation of
        this method.

        Parameters
        ----------
        distribution : {'uniform', 'integer'}
                       the distribution to sample from
        params : tuple
                 the parameters specifying the distribution
        size : int
               the number of samples to generate

        Returns
        -------
        numpy array
            the samples for the distribution and specified parameters

        '''

    def generate_samples(self, parameters, size):
        '''
        The main method of :class: `~sampler.Sampler` and its
        children. This will call the sample method for each of the
        parameters and return the resulting designs.

        Parameters
        ----------
        parameters : collection
                     a collection of :class:`~parameters.Parameterparamertainty`
                     and :class:`~parameters.Categoricalparamertainty`
                     instances.
        size : int
               the number of samples to generate.


        Returns
        -------
        dict
            dict with the paramertainty.name as key, and the sample as value

        '''
        return {param.name: self.sample(param.dist, param.params, size) for
                param in parameters}

    def generate_designs(self, parameters, nr_samples):
        '''external interface to sampler. Returns the computational experiments
        over the specified parameters, for the given number of samples for each
        parameter.

        Parameters
        ----------
        parameters : list
                        a list of parameters for which to generate the
                        experimental designs
        nr_samples : int
                     the number of samples to draw for each parameter


        Returns
        -------
        generator
            a generator object that yields the designs resulting from
            combining the parameters
        int
            the number of experimental designs

        '''
        parameters = sorted(parameters, key=operator.attrgetter('name'))
        sampled_parameters = self.generate_samples(parameters, nr_samples)
        designs = zip(*[sampled_parameters[u.name] for u in parameters])
        designs = DefaultDesigns(designs, parameters, nr_samples)

        return designs


class LHSSampler(AbstractSampler):
    """
    generates a Latin Hypercube sample for each of the parameters
    """

    def __init__(self):
        super(LHSSampler, self).__init__()

    def sample(self, distribution, params, size):
        '''
        generate a Latin Hypercube Sample.

        Parameters
        ----------
        distribution : scipy distribution
                       the distribution to sample from
        params : tuple
                 the parameters specifying the distribution
        size : int
               the number of samples to generate

        Returns
        -------
        dict
            with the paramertainty.name as key, and the sample as value

        '''

        return self._lhs(self.distributions[distribution], params, size)

    def _lhs(self, dist, parms, siz):
        '''
        Latin Hypercube sampling of any distribution.

        Parameters
        ----------
        dist : random variable distribution from `scipy.stats <http://docs.scipy.org/doc/scipy/reference/stats.html>`_
        parms : tuple
                tuple of parameters as required for dist.
        siz : int
              number of samples

        '''
        perc = np.linspace(0, (siz-1)/siz, siz)
        np.random.shuffle(perc)
        smp = stats.uniform(perc, 1. / siz).rvs()
        v = dist(*parms).ppf(smp)

        return v


class UniformLHSSampler(AbstractSampler):
    """
    generates a LHS for the parameters, ignoring defined distribution shapes
    """

    def __init__(self):
        super(UniformLHSSampler, self).__init__()

    def sample(self, distribution, params, size):
        '''
        generate a Latin Hypercube Sample.

        Parameters
        ----------
        distribution : scipy distribution
                       the distribution to sample from
        params : tuple
                 the parameters specifying the distribution
        size : int
               the number of samples to generate

        Returns
        -------
        dict
            with the paramertainty.name as key, and the sample as value

        '''

        return self._lhs(self.uniform_distributions[distribution], params[-2:], size)

    def _lhs(self, dist, parms, siz):
        '''
        Latin Hypercube sampling of any distribution.

        Parameters
        ----------
        dist : random variable distribution from `scipy.stats <http://docs.scipy.org/doc/scipy/reference/stats.html>`_
        parms : tuple
                tuple of parameters as required for dist.
        siz : int
              number of samples

        '''
        perc = np.linspace(0, (siz-1)/siz, siz)
        np.random.shuffle(perc)
        smp = stats.uniform(perc, 1. / siz).rvs()
        v = dist(*parms).ppf(smp)

        return v


class MonteCarloSampler(AbstractSampler):
    """
    generates a Monte Carlo sample for each of the parameters.

    """

    def __init__(self):
        super(MonteCarloSampler, self).__init__()

    def sample(self, distribution, params, size):
        '''
        generate a Monte Carlo Sample.

        Parameters
        ----------
        distribution : scipy distribution
                       the distribution to sample from
        params : 2-tuple of floats
                 the parameters specifying the distribution
        size : int
               the number of samples to generate

        Returns
        -------
        dict
            with the paramertainty.name as key, and the sample as value

        '''

        return self.distributions[distribution](*params).rvs(size)


class FullFactorialSampler(AbstractSampler):
    '''
    generates a full factorial sample.

    If the parameter is non categorical, the resolution is set the
    number of samples. If the parameter is categorical, the specified value
    for samples will be ignored and each category will be used instead.

    '''

    def __init__(self):
        super(FullFactorialSampler, self).__init__()

    def generate_samples(self, parameters, size):
        '''
        The main method of :class: `~sampler.Sampler` and its
        children. This will call the sample method for each of the
        parameters and return the resulting samples

        Parameters
        ----------
        parameters : collection
                        a collection of :class:`~parameters.Parameter`
                        instances
        size : int
                the number of samples to generate.

        Returns
        -------
        dict
            with the paramertainty.name as key, and the sample as value
        '''
        samples = {}
        for param in parameters:
            cats = param.resolution
            if not cats:
                cats = np.linspace(param.lower_bound,
                                   param.upper_bound,
                                   size)
                if isinstance(param, IntegerParameter):
                    cats = np.round(cats, 0)
                    cats = set(cats)
                    cats = (int(entry) for entry in cats)
                    cats = sorted(cats)
            samples[param.name] = cats

        return samples

    def generate_designs(self, parameters, nr_samples):
        '''
        This method provides an alternative implementation to the default
        implementation provided by :class:`~sampler.Sampler`. This
        version returns a full factorial design across the parameters.

        Parameters
        ----------
        parameters : list
                        a list of parameters for which to generate the
                        experimental designs
        nr_samples : int
                     the number of intervals to use on each
                     Parameter. Categorical parameters always
                     return all their categories

        Returns
        -------
        generator
            a generator object that yields the designs resulting from
            combining the parameters
        int
            the number of experimental designs

        '''
        parameters = sorted(parameters, key=operator.attrgetter('name'))

        samples = self.generate_samples(parameters, nr_samples)
        zipped_samples = itertools.product(
            *[samples[u.name] for u in parameters])

        n_designs = self.determine_nr_of_designs(samples)
        designs = DefaultDesigns(zipped_samples, parameters, n_designs)

        return designs

    def determine_nr_of_designs(self, sampled_parameters):
        '''
        Helper function for determining the number of experiments that will
        be generated given the sampled parameters.

        Parameters
        ----------
        sampled_parameters : list
                        a list of sampled parameters, as
                        the values return by generate_samples

        Returns
        -------
        int
            the total number of experimental design
        '''
        nr_designs = 1
        for value in sampled_parameters.values():
            nr_designs *= len(value)
        return nr_designs


class PartialFactorialSampler(AbstractSampler):
    """
    generates a partial factorial design over the parameters. Any parameter
    where factorial is true will be included in a factorial design, while the
    remainder will be sampled using LHS or MC sampling.

    Parameters
    ----------
    sampling: {PartialFactorialSampler.LHS, PartialFactorialSampler.MC}, optional
              the desired sampling for the non factorial parameters.

    Raises
    ------
    ValueError
        if sampling is not either LHS or MC

    """

    LHS = 'LHS'
    MC = 'MC'

    def __init__(self, sampling='LHS'):
        super(PartialFactorialSampler, self).__init__()

        if sampling == PartialFactorialSampler.LHS:
            self.sampler = LHSSampler()
        elif sampling == PartialFactorialSampler.MC:
            self.sampler = MonteCarloSampler()
        else:
            raise ValueError(('invalid value for sampling type, should be LHS '
                              'or MC'))
        self.ff = FullFactorialSampler()

    def _sort_parameters(self, parameters):
        '''sort parameters into full factorial and other

        Parameters
        ----------
        parameters : list of parameters

        '''
        ff_params = []
        other_params = []

        for param in parameters:
            if param.pff:
                ff_params.append(param)
            else:
                other_params.append(param)

        return ff_params, other_params

    def generate_designs(self, parameters, nr_samples):
        '''external interface to sampler. Returns the computational experiments
        over the specified parameters, for the given number of samples for each
        parameter.

        Parameters
        ----------
        parameters : list
                        a list of parameters for which to generate the
                        experimental designs
        nr_samples : int
                     the number of samples to draw for each parameter

        Returns
        -------
        generator
            a generator object that yields the designs resulting from
            combining the parameters
        int
            the number of experimental designs

        '''

        ff_params, other_params = self._sort_parameters(parameters)

        # generate a design over the factorials
        # TODO update ff to use resolution if present
        ff_designs = self.ff.generate_designs(ff_params, nr_samples)

        # generate a design over the remainder
        # for each factorial, run the MC design
        other_designs = self.sampler.generate_designs(other_params,
                                                      nr_samples)

        nr_designs = other_designs.n * ff_designs.n

        designs = PartialFactorialDesigns(ff_designs, other_designs,
                                          parameters, nr_designs)

        return designs


def determine_parameters(models, attribute, union=True):
    '''determine the parameters over which to sample

    Parameters
    ----------
    models : a collection of AbstractModel instances
    attribute : {'uncertainties', 'levers'}
    union : bool, optional
            in case of multiple models, sample over the union of
            levers, or over the intersection of the levers
    sampler : Sampler instance, optional

    Returns
    -------
    collection of Parameter instances

    '''
    return util.determine_objects(models, attribute, union=union)


def sample_levers(models, n_samples, union=True, sampler=LHSSampler(),
                  name=util.representation):
    '''generate policies by sampling over the levers

    Parameters
    ----------
    models : a collection of AbstractModel instances
    n_samples : int
    union : bool, optional
            in case of multiple models, sample over the union of
            levers, or over the intersection of the levers
    sampler : Sampler instance, optional
    name : callable, optional
           a callable to generate a name given the sampled values
          for each lever

    Returns
    -------
    generator yielding Policy instances

    '''
    levers = determine_parameters(models, 'levers', union=union)
    samples = sampler.generate_designs(levers, n_samples)

    partial_policy = functools.partial(Policy, name=name)
    samples.kind = partial_policy

    return samples


def sample_uncertainties(models, n_samples, union=True, sampler=LHSSampler()):
    '''generate scenarios by sampling over the uncertainties

    Parameters
    ----------
    models : a collection of AbstractModel instances
    n_samples : int
    union : bool, optional
            in case of multiple models, sample over the union of
            uncertainties, or over the intersection of the uncertainties
    sampler : Sampler instance, optional

    Returns
    -------
    generator
        yielding Scenario instances
    collection
        the collection of parameters over which to sample
    n_samples
        the number of scenarios (!= n_samples in case off FF sampling)


    '''
    uncertainties = determine_parameters(models, 'uncertainties', union=union)
    samples = sampler.generate_designs(uncertainties, n_samples)
    samples.kind = Scenario

    return samples

def sample_parameters(models, n_samples, union=True, sampler=LHSSampler()):
    '''generate scenarios by sampling over the uncertainties

    Parameters
    ----------
    models : a collection of AbstractModel instances
    n_samples : int
    union : bool, optional
            in case of multiple models, sample over the union of
            uncertainties, or over the intersection of the uncertainties
    sampler : Sampler instance, optional

    Returns
    -------
    generator
        yielding Scenario instances
    collection
        the collection of parameters over which to sample
    n_samples
        the number of scenarios (!= n_samples in case off FF sampling)


    '''
    parms = determine_parameters(models, 'uncertainties', union=union) + determine_parameters(models, 'levers', union=union)
    samples = sampler.generate_designs(parms, n_samples)
    samples.kind = Scenario

    return samples


def from_experiments(models, experiments):
    '''generate scenarios from an existing experiments DataFrame

    Parameters
    ----------
    models : collection of AbstractModel instances
    experiments : DataFrame

    Returns
    -------
     generator
        yielding Scenario instances

    '''
    policy_names = np.unique(experiments['policy'])
    model_names = np.unique(experiments['model'])

    # we sample ff over models and policies so we need to ensure
    # we only get the experiments for a single model policy combination
    logical = (experiments['model'] == model_names[0]) & \
              (experiments['policy'] == policy_names[0])

    experiments = experiments[logical]

    uncertainties = util.determine_objects(models, 'uncertainties',
                                           union=True)
    samples = {unc.name: experiments[:, unc.name] for unc in
               uncertainties}

    scenarios = DefaultDesigns(samples, uncertainties,
                               experiments.shape[0])
    scenarios.kind = Scenario

    return scenarios


class DefaultDesigns(object):
    '''iterable for the experimental designs'''

    def __init__(self, designs, parameters, n):
        self.designs = list(designs)
        self.parameters = parameters
        self.params = [p.name for p in parameters]
        self.kind = None
        self.n = n

    @abc.abstractmethod
    def __iter__(self):
        '''should return iterator'''

        return design_generator(self.designs, self.parameters, self.kind)

    def __repr__(self):
        return f"<ema_workbench.DefaultDesigns, {self.n} designs on {len(self.params)} parameters>"


class PartialFactorialDesigns(object):

    @property
    def kind(self):
        return self._kind

    @kind.setter
    def kind(self, value):
        self._kind = value
        self.ff_designs.kind = value
        self.other_designs.kind = value

    def __init__(self, ff_designs, other_designs, parameters, n):
        self.ff_designs = ff_designs
        self.other_designs = other_designs

        self.parameters = parameters
        self.params = [p.name for p in parameters]

        self._kind = None
        self.n = n

    def __iter__(self):
        designs = itertools.product(self.ff_designs, self.other_designs)
        return partial_designs_generator(designs)


def partial_designs_generator(designs):
    '''generator which combines the full factorial part of the design
    with the non full factorial part into a single dict

    Parameters
    ----------
    designs: iterable of tuples

    Yields
    ------
    dict
        experimental design dict


    '''

    for design in designs:
        ff_part, other_part = design

        design = ff_part.copy()
        design.update(other_part)

        yield design


def design_generator(designs, params, kind):
    '''generator that combines the sampled parameters with their correct
    name in order to return dicts.

    Parameters
    ----------
    designs : iterable of tuples
    params : iterable of str

    Yields
    ------
    dict
        experimental design dictionary

    '''

    for design in designs:

        design_dict = {}
        for param, value in zip(params, design):
            if isinstance(param, CategoricalParameter):
                # categorical parameter is an integer parameter, so
                # conversion to int is already done
                if isinstance(value, Category):
                    value = value.value
                if value not in param.categories:
                    value = param.cat_for_index(int(value)).value
            elif isinstance(param, IntegerParameter):
                value = int(value)
            elif isinstance(param, BooleanParameter):
                value = bool(int(value))

            design_dict[param.name] = value

        yield kind(**design_dict)
