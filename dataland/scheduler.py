import os
import sys
import yaml
import logging
import importlib
import pandas as pd

from datetime import timedelta, datetime, time
from dataland.file_utils import lastest_file, timestamped_file
from dataland.dataset import get_template

DATETIME_FORMAT = '%Y-%m-%d-%H%M'
FREQUENCY_MAPINGS = {
    '1h':     timedelta(hours=1),
    '4h':     timedelta(hours=4),
    '8h':     timedelta(hours=8),
    '12h':    timedelta(hours=12),
    'daily':  timedelta(days=1),
    'weekly': timedelta(days=7),
}

SUCCESS_STATUS = 'SUCCESS'
FAILURE_STATUS = 'FAILED'

class Job(object):
    def __init__(self, operations=[]):
        self.operations = operations

    def run(self):
        for operation in self.operations:
            operation.perform()

class Operation(object):
    def perform(self):
        raise NotImplemented

class AppendOperation(Operation):
    INPUT = ''
    IGNORE_DUPLICATES=False # requires full dataset to be read into memory

    def perform(self):
        if not os.path.isfile(self.__class__.INPUT):
            raise ValueError('Operation INPUT "{}" is not a valid path'.format(INPUT))


        new_records = self.new_records()

        if self.__class__.IGNORE_DUPLICATES:
            # TODO this does not work as expected
            # it should read the old dataframe and only select new records
            old_records = pd.read_csv(self.__class__.INPUT)
            new_records = old_records.concat(new_records).drop_duplicates()

        assert (new_records.columns.values ==  self.get_template().columns.values).all(), 'new_records do not match existing data template'
        with open(self.__class__.INPUT, 'a') as input_file:
            new_records.to_csv(input_file, header=False, index=False)

        logging.info('{} updated {} records to {}'.format(self.__class__.__name__, len(new_records), self.__class__.INPUT))

    def new_records(self):
        raise NotImplemented
        '''
        returns a dataframe containing new records to be appended
        '''

    def get_template(self):
        return get_template(self.__class__.INPUT)

class TransformOperation(Operation):
    INPUT=''
    OUTPUT=''

    def perform(self):
        if not os.path.isfile(self.__class__.INPUT):
            raise ValueError('Operation INPUT "{}" is not a valid path'.format(INPUT))

        input_dataframe = pd.read_csv(self.__class__.INPUT)
        input_dataframe = self.update(input_dataframe)

        with open(self.__class__.INPUT, 'a') as input_file:
            input_dataframe.to_csv(self.__class__.OUTPUT, mode='w+', index=False, float_format='%.4f')

        logging.info('{} updated {} records to {}'.format(self.__class__.__name__, len(input_dataframe), self.__class__.OUTPUT))

    def transform(self, input_dataframe):
        raise NotImplemented
        '''
        returns new output dataframe
        '''

class UpdateOperation(TransformOperation):

    def __init__(self, *args, **kwargs):
        self.__class__.OUTPUT=self.__class__.INPUT
        self.transform = self.update
        super(UpdateOperation, self).__init__(*args, **kwargs)

    def update(self, input_dataframe):
        raise NotImplemented
        '''
        returns updated version of `input_dataframe`
        '''

class Scheduler(object):
    def __init__(self, schedule_file='config/schedule.yml', log_dir='logs'):
        self.log_dir = log_dir

        self._load_schedule(schedule_file)
        self._load_schedule_history()

    def run(self):
        pending_jobs = self._pending_jobs()
        if len(pending_jobs) == 0:
            return

        self._configure_logging()
        for name, job in pending_jobs.items():
            try:
                module = importlib.import_module(job['module'])
                logging.info('Scheduler running {}'.format(job['module']))
                module.job.run()
            except Exception as e:
                self._mark_job_failure(name)
                logging.error('Scheduler failed {}'.format(job['module']))
                logging.error(e.message)

            self._mark_job_sucess(name)
            logging.info('Scheduler succedded {}'.format(job['module']))

        self._save_schedule_history()
        logging.shutdown()


    def _load_schedule(self, file):
        with open(file, 'r') as schedule:
            self.schedule = yaml.load(schedule)

    def _load_schedule_history(self):
        self.history = {}
        for job_name in self.schedule.keys():
            self.history[job_name] = {
                'last_run': '0001-01-01-0000',
                'status': None
            }

        with lastest_file(os.path.join(self.log_dir, 'history'), type='yml', mode='r') as schedule_history:
            history = yaml.load(schedule_history)
            if history != None:
                self.history.update(history)

    def _save_schedule_history(self):
        with timestamped_file(os.path.join(self.log_dir, 'history'), type='yml', mode='w+') as schedule_file:
            schedule_file.write(yaml.dump(self.history, default_flow_style=False))

    def _pending_jobs(self):
        jobs = {}
        for name, job in self.schedule.items():
            if job.has_key('after') and datetime.now().time() < datetime.strptime(job['after'], '%H:%M').time():
                continue
            if job.has_key('before') and datetime.now().time() > datetime.strptime(job['before'], '%H:%M').time():
                continue

            next_run = datetime.strptime(self.history[name]['last_run'], DATETIME_FORMAT) + FREQUENCY_MAPINGS[job['frequency']]
            if datetime.now() > next_run or self.history[name]['status'] != SUCCESS_STATUS:
                jobs[name] = job
        logging.info('Scheduler has {} pending job(s)'.format(len(jobs)))
        return jobs

    def _mark_job_sucess(self, job_name):
        self.history[job_name]['last_run'] = datetime.strftime(datetime.now(), DATETIME_FORMAT)
        self.history[job_name]['status'] = SUCCESS_STATUS

    def _mark_job_failure(self, job_name):
        self.history[job_name]['last_run'] = datetime.strftime(datetime.now(), DATETIME_FORMAT)
        self.history[job_name]['status'] = FAILURE_STATUS

    def _configure_logging(self):
        formatter = logging.Formatter(
            fmt='[%(levelname)s] %(asctime)s.%(msecs)03d | %(message)s',
            datefmt='%Y-%m-%d %I:%M:%-S'
        )
        file_stream = logging.StreamHandler(stream=timestamped_file(os.path.join(self.log_dir, 'scheduler'), type='log', mode='a+'))
        file_stream.setFormatter(formatter)
        file_stream.setLevel(20)
        console_stream = logging.StreamHandler(stream=sys.stdout)
        console_stream.setFormatter(formatter)
        console_stream.setLevel(30)

        logging.getLogger().setLevel(20)
        logging.getLogger().addHandler(file_stream)
        logging.getLogger().addHandler(console_stream)

if __name__ == '__main__':
    Scheduler().run()
