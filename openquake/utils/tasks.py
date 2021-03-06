# -*- coding: utf-8 -*-
# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2010-2012, GEM Foundation.
#
# OpenQuake is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# OpenQuake is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.


"""Utility functions related to splitting work into tasks."""

import itertools
from celery.task.sets import TaskSet

from openquake import logs


def distribute(task_func, (name, data), tf_args=None, ath=None, ath_args=None,
               flatten_results=False):
    """Runs `task_func` for each of the given data items.

    Each subtask operates on an item drawn from `data`. It is up to
    the caller to provide a collection that yields data as expected
    by the task function.

    Please note that for tasks with ignore_result=True
        - no results are returned
        - the control flow returns to the caller immediately i.e. this
          function does *not* block while the tasks are running unless
          the caller specifies an asynchronous task handler function.
        - if specified, an asynchronous task handler function (`ath`)
          will be run as soon as the tasks have been started.
          It can be used to check/wait for task results as appropriate
          and is likely to execute in parallel with longer running tasks.

    :param task_func: A `celery` task callable.
    :param str name: The name of the `task_func` parameter used to pass the
        data item.
    :param data: The data on which the subtasks are to operate.
    :param dict tf_args: The remaining (keyword) parameters for `task_func`
    :param ath: an asynchronous task handler function, may only be specified
        for a task whose results are ignored.
    :param dict ath_args: The keyword parameters for `ath`
    :param bool flatten_results: If set, the results will be returned as a
        single list (as opposed to [[results1], [results2], ..]).
    :returns: A list where each element is a result returned by a subtask.
        If an `ath` function is passed we return whatever it returns, `None`
        otherwise.
    """
    logs.HAZARD_LOG.debug("-data_length: %s" % len(data))

    subtask = task_func.subtask
    if tf_args:
        subtasks = [subtask(**dict(tf_args.items() + [(name, item)]))
                    for item in data]
    else:
        subtasks = [subtask(**{name: item}) for item in data]

    logs.HAZARD_LOG.debug("-#subtasks: %s" % len(subtasks))

    result = TaskSet(tasks=subtasks).apply_async()
    if task_func.ignore_result:
        # Did the user specify an asynchronous task handler function?
        if ath:
            if ath_args:
                return ath(**ath_args)
            else:
                return ath()
    else:
        # Only called when we expect result messages to come back.
        results = result.join_native()
        _check_exception(results)
        if results and flatten_results:
            sample = results[0]
            if isinstance(sample, (list, tuple, set)):
                results = list(itertools.chain(*results))
        return results


def _check_exception(results):
    """If any of the results is an exception, raise it."""
    for result in results:
        if isinstance(result, Exception):
            raise result


class JobCompletedError(Exception):
    """
    Exception to be thrown by :func:`get_running_job`
    in case of dealing with already completed job.
    """


def get_running_job(job_id):
    """Helper function which is intended to be run by celery task functions.

    Given the id of an in-progress calculation
    (:class:`openquake.db.models.OqJob`), load all of the calculation
    data from the database and KVS and return a
    :class:`openquake.engine.JobContext` object.

    If the calculation is not currently running, a
    :exception:`JobCompletedError` is raised.

    :returns:
        :class:`openquake.engine.JobContext` object, representing an
        in-progress job. This object is created from cached data in the
        KVS as well as data stored in the relational database.
    :raises JobCompletedError:
        If :meth:`~openquake.engine.JobContext.is_job_completed` returns
        ``True`` for ``job_id``.
    """
    # pylint: disable=W0404
    from openquake.engine import JobContext

    if JobContext.is_job_completed(job_id):
        raise JobCompletedError(job_id)

    job_ctxt = JobContext.from_kvs(job_id)
    if job_ctxt and job_ctxt.params:
        level = job_ctxt.log_level
    else:
        level = 'warn'
    logs.init_logs_amqp_send(level=level, job_id=job_id)

    return job_ctxt


def calculator_for_task(job_id, job_type):
    """Given the id of an in-progress calculation
    (:class:`openquake.db.models.OqJob`), load all of the calculation
    data from the database and KVS and instantiate the calculator required for
    a task's computation.

    :param int job_id:
        id of a in-progress job.
    :params job_type:
        'hazard' or 'risk'
    :returns:
        An instance of a calculator classed. The type of calculator depends on
        the ``job_type`` (hazard or risk) and the calculation mode (classical,
        event based, etc.).
    :rtype:
        Subclass of :class:`openquake.calculators.base.Calculator`.
    :raises JobCompletedError:
        If the specified calculation is not currently running.
    """
    # pylint: disable=W0404
    from openquake.engine import CALCS

    job_ctxt = get_running_job(job_id)
    calc_mode = job_ctxt.oq_job_profile.calc_mode
    calculator = CALCS[job_type][calc_mode](job_ctxt)

    return calculator
