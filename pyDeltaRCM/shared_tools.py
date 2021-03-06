
import numpy as np

from numba import njit, jit, typed, _helperlib

# tools shared between deltaRCM water and sediment routing


def get_iwalk():
    return np.array([[-1, 0, 1],
                     [-1, 0, 1],
                     [-1, 0, 1]], dtype=np.int64)


def get_jwalk():
    return np.array([[-1, -1, -1],
                     [0, 0, 0],
                     [1, 1, 1]], dtype=np.int64)


@njit
def set_random_seed(_seed):
    np.random.seed(_seed)


def get_random_state():
    ptr = _helperlib.rnd_get_np_state_ptr()
    return _helperlib.rnd_get_state(ptr)


def set_random_state(_state_tuple):
    ptr = _helperlib.rnd_get_np_state_ptr()
    _helperlib.rnd_set_state(ptr, _state_tuple)


@njit
def get_random_uniform(N):
    return np.random.uniform(0, 1)


@njit
def get_start_indices(inlet, inlet_weights, num_starts):
    norm_weights = inlet_weights / np.sum(inlet_weights)
    idxs = []
    for x in np.arange(num_starts):
        idxs.append(random_pick(norm_weights))
    idxs = np.array(idxs)
    return inlet.take(idxs)


@njit
def get_steps(new_cells, iwalk, jwalk):
    """Find the values giving the next step."""
    istep = iwalk[new_cells]
    jstep = jwalk[new_cells]
    dist = np.sqrt(istep * istep + jstep * jstep)

    astep = dist != 0

    return dist, istep, jstep, astep


@njit
def random_pick(prob):
    """Pick number from weighted array.

    Randomly pick a number weighted by array probabilities (len 9)
    Return the index of the selected weight in array probs
    Takes a numpy array that is the precalculated cumulative probability
    around the cell flattened to 1D.
    """
    arr = np.arange(len(prob))
    return arr[np.searchsorted(np.cumsum(prob), get_random_uniform(1))]


@njit
def custom_unravel(i, shape):
    """Unravel indexes for 2D array."""
    if i > (shape[1] * shape[0]):
        raise IndexError("Index is out of matrix bounds")
    x = i // shape[1]
    y = i % shape[1]
    return x, y


@njit
def custom_ravel(tup, shape):
    """Ravel indexes for 2D array."""
    if tup[0] > shape[0] or tup[1] > shape[1]:
        raise IndexError("Index is out of matrix bounds")
    x = tup[0] * shape[1]
    y = tup[1]
    return x + y


@njit
def get_weight_sfc_int(stage, stage_nbrs, qx, qy, ivec, jvec, distances):
    """Determine random walk weight surfaces.

    Determines the surfaces for weighting the random walks based on the stage
    and discharge fields.
    """
    weight_sfc = np.maximum(0, (stage - stage_nbrs) / distances)
    weight_int = np.maximum(0, (qx * jvec + qy * ivec) / distances)
    return weight_sfc, weight_int


@njit
def get_weight_at_cell(ind, weight_sfc, weight_int, depth_nbrs, ct_nbrs,
                       dry_depth, gamma, theta):

    if ind[0] == 0:
        weight_sfc[:3] = np.nan
        weight_int[:3] = np.nan

    drywall = (depth_nbrs <= dry_depth) | (ct_nbrs == -2)
    weight_sfc[drywall] = np.nan
    weight_int[drywall] = np.nan

    if np.nansum(weight_sfc) > 0:
        weight_sfc = weight_sfc / np.nansum(weight_sfc)

    if np.nansum(weight_int) > 0:
        weight_int = weight_int / np.nansum(weight_int)

    weight = gamma * weight_sfc + (1 - gamma) * weight_int
    weight = depth_nbrs ** theta * weight
    weight[depth_nbrs <= dry_depth] = 0

    nanWeight = np.isnan(weight)

    if np.any(weight[~nanWeight] != 0):
        weight = weight / np.nansum(weight)
        weight[nanWeight] = 0
    else:
        weight[~nanWeight] = 1 / np.maximum(1, len(weight[~nanWeight]))
        weight[nanWeight] = 0
    return weight


def _get_version():
    """Extract version from file.

    Extract version number from single file, and make it availabe everywhere.
    """
    from . import _version
    return _version.__version__()


def scale_model_time(time, If=1, units='seconds'):
    """Scale the model time to "real" time.

    Model time is executed as assumed flooding conditions, and executed at the
    per-second level, with a multi-second timestep. This model design
    implicitly assumes that the delta is *always* receiving a large volume of
    sediment and water at the inlet. This is unrealistic, given that rivers
    flood only during a small portion of the year, and this is when
    morphodynamic activity is largest. See :doc:`../../info/modeltime` for a
    complete description of this assumption, and how to work with the
    assumption in configuring the model.

    Using this assumption, it is possible to scale up model time to "real"
    time, by assuming an *intermittency factor*. This intermittency factor is
    the fraction of unit-time that a river is assumed to be flooding.

    .. math::

        t_r = \dfrac{t}{I_f \cdot S_f}

    where :math:`t` is the model time (:obj:`~pyDeltaRCM.DeltaModel.time`),
    :math:`t_r` is the "real" scaled time, :math:`I_f` is the
    intermittency factor, and :math:`S_f` is the scale factor to convert base
    units of seconds to units specified as an input argument. Note that this
    function uses :obj:`_scale_factor` internally for this conversion.

    Parameters
    ----------
    time : :obj:`float`
        The model time, in seconds.

    If : :obj:`float`, optional
        Intermittency factor, fraction of time represented by morphodynamic
        activity. Should be in interval (0, 1]. Defaults to 1 if not provided,
        i.e., no scaling is performed.

    units : :obj:`str`, optional
        The units to convert the scaled time to. Default is to return the
        scaled time in seconds (`seconds`), but optionally supply argument
        `days` or `years` for unit conversion.

    Returns
    -------
    scaled : :obj:`float`
        Scaled time, in :obj:`units`, assuming the intermittency factor
        :obj:`If`.

    Raises
    ------
    ValueError
        if the value for intermittency is not ``0 < If <= 1``.
    """
    if (If <= 0) or (If > 1):
        raise ValueError(
            'Intermittency `If` is not 0 < If <= 1: %s' % str(If))

    return time / _scale_factor(If, units)


def _scale_factor(If, units):
    """Scaling factor between model time and "real" time.

    The scaling factor relates the model time to a real worl time, by the
    assumed intermittency factor and the user-specified units for output.

    Parameters
    ----------
    If : :obj:`float`
        Intermittency factor, fraction of time represented by morphodynamic
        activity. **Must** be in interval (0, 1].

    units : :obj:`str`
        The units to convert the scaled time to. Must be a string in
        `['seconds', 'days', 'years']`.

    """
    sec_in_day = 86400
    day_in_yr = 365.25
    if units == 'seconds':
        S_f = 1
    elif units == 'days':
        S_f = sec_in_day
    elif units == 'years':
        S_f = sec_in_day * day_in_yr
    else:
        raise ValueError('Bad value for `units`: %s' % str(units))
    return (If * S_f)
