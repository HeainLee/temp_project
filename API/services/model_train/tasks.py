'''
celery로 처리할 작업을 정의
스마트시티 분석 모듈에서는 모델학습(TRAIN MODEL) 작업을 처리할 때 사용 
@shared_task 데코레이터 = 해당 함수에 대한 요청이 들어오며 작업을 할당
'''
from __future__ import absolute_import
from multiprocessing import current_process

try:
    current_process()._config
except AttributeError:
    current_process()._config = {'semprefix': '/mp'}

import logging
import datetime
from celery import shared_task
from smartcity.celery import app

from .train_helper import SklearnTrainTask
from ...models.train_info import TrainInfo
from ...serializers.serializers import TrainModelSerializer

logger = logging.getLogger('collect_log_task')


@shared_task(name='train_tasks.model_train', bind=True, ignore_result=False, track_started=True)
def model_train(self, train_info=None, data_saved_path=None, pk=None, mode=None):
    logger.info('요청 ID [{}]의 모델 학습이 진행중입니다'.format(pk))
    back_job = model_train_result(
        train_info=train_info, data_saved_path=data_saved_path, pk=pk, mode=mode)
    train_info = TrainInfo.objects.get(pk=pk)
    train_infomation = {}

    if back_job == False:
        train_infomation['PROGRESS_STATE'] = 'fail'
        train_infomation['PROGRESS_END_DATETIME'] = datetime.datetime.now()
        logger.error('요청 ID [{}]의 모델 학습이 실패했습니다'.format(pk))
        serializer = TrainModelSerializer(train_info, data=train_infomation, partial=True)

    else:
        train_infomation['FILEPATH'] = str(back_job['file_path'])
        train_infomation['FILENAME'] = str(back_job['file_name'])
        train_infomation['TRAIN_SUMMARY'] = str(back_job['model_info'])
        train_infomation['VALIDATION_SUMMARY'] = str(back_job['validation_info'])
        train_infomation['PROGRESS_STATE'] = 'success'
        train_infomation['LOAD_STATE'] = 'load_available'
        train_infomation['PROGRESS_END_DATETIME'] = datetime.datetime.now()
        logger.info('요청 ID [{}]의 모델 학습이 완료되었습니다'.format(pk))
        serializer = TrainModelSerializer(
            train_info, data=train_infomation, partial=True)
    if serializer.is_valid():
        serializer.save()
        return 'async_task_finished'
    else:
        logger.info('요청 ID [{}]의 모델 저장이 실패했습니다 [모델 학습 정보] = {}' \
                    .format(pk, train_infomation))
        return 'save_failed'


def model_train_result(train_info=None, data_saved_path=None, pk=None, mode=None):
    sk_asyn_task = SklearnTrainTask()

    try:
        # time.sleep(15)
        logger.info('요청 ID [{}]의 모델 학습 모드는 [{}] 입니다'.format(pk, mode))

        model_param = train_info['model_parameters'] \
            if 'model_parameters' in train_info.keys() else None
        train_param = train_info['train_parameters']

        if mode == 'restart':
            model_param = sk_asyn_task.check_params(params_dict=model_param)
            train_param = sk_asyn_task.check_params(params_dict=train_param)

        sklearn_result = sk_asyn_task.model_task_result(
            algo_pk=train_info['algorithms_sequence_pk'],
            data_path=data_saved_path,
            model_param=model_param,
            train_param=train_param,
            pk=pk
        )

        return sklearn_result

    except Exception as e:
        logger.error('Error Type = {} / Error Message = {}'.format(type(e), e))
        return False
        