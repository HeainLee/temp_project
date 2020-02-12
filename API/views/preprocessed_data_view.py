#####################[전처리 데이터 관리]######################
import os
import glob
import zipfile
import logging
import datetime
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from ..models.original_data import OriginalData
from ..models.preprocess_functions import PreprocessFunction
from ..models.preprocessed_data import PreprocessedData
from ..serializers.serializers import PreprocessedDataSerializer
from ..services.data_preprocess import tasks
from ..services.data_preprocess.preprocess_helper import CreatePreprocessedData
from ..services.utils.custom_response import CustomErrorCode
from ..config.result_path_config import PATH_CONFIG

logger = logging.getLogger('collect_log_view')
error_code = CustomErrorCode()


class PreprocessedDataView(APIView):
    def post(self, request):  # 실제 전처리 수행은 celery task로 전달
        user_request = request.data
        create_data = CreatePreprocessedData()

        check_result = create_data.check_request_body_preprocessed_post(
            request_info=user_request)
        if not check_result:
            error_type = check_result['error_type']
            error_msg = check_result['error_msg']
            if error_type == '4004':
                if error_msg.split(',')[1] == 'original_data':
                    get_object_or_404(OriginalData, pk=error_msg.split(',')[0])
                elif error_msg.split(',')[1] == 'preprocess_function':
                    get_object_or_404(PreprocessFunction, pk=error_msg.split(',')[0])
                elif error_msg.split(',')[1] == 'file_not_found':
                    return Response(error_code.FILE_NOT_FOUND_4004(path_info=error_msg.split(',')[0]),
                                    status=status.HTTP_404_NOT_FOUND)
            elif error_type == '4101':
                return Response(error_code.MANDATORY_PARAMETER_MISSING_4101(error_msg),
                                status=status.HTTP_400_BAD_REQUEST)
            elif error_type == '4102':
                return Response(error_code.INVALID_PARAMETER_TYPE_4102(error_msg),
                                status=status.HTTP_400_BAD_REQUEST)

        query = PreprocessedData.objects.all()
        if query.exists():
            get_pk_new = PreprocessedData.objects.latest('PREPROCESSED_DATA_SEQUENCE_PK').pk + 1
        else:
            get_pk_new = 1

        result = tasks.transformer_fit.apply_async(
            args=[create_data.data_saved_path, user_request, get_pk_new])
        logger.info('요청 ID [{}]의 전처리 작업을 시작합니다'.format(get_pk_new))
        info_save = dict(
            COMMAND=str(request.data),
            PROGRESS_STATE='ongoing',
            PROGRESS_START_DATETIME=datetime.datetime.now(),
            COLUMNS='N/A', STATISTICS='N/A', SAMPLE_DATA='N/A', AMOUNT=0,
            ORIGINAL_DATA_SEQUENCE_FK1=create_data.original_data_pk,
        )

        serializer = PreprocessedDataSerializer(data=info_save)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_202_ACCEPTED)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request):
        queryset = PreprocessedData.objects.all().order_by('PREPROCESSED_DATA_SEQUENCE_PK')
        serializer = PreprocessedDataSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PreprocessedDataDetailView(APIView):
    def get(self, request, pk):
        preprocessed_data = get_object_or_404(PreprocessedData, pk=pk)
        serializer = PreprocessedDataSerializer(preprocessed_data)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        # DELETE_FLAG == True로 전환하고, 저장된 파일 삭제 (db의 instance는 삭제하지 않음)
        # 이미 DELETE_FLAG가 True인 경우, Conflict(4009) 에러 반환
        preprocessed_data = get_object_or_404(PreprocessedData, pk=pk)
        serializer = PreprocessedDataSerializer(preprocessed_data)
        if serializer.data['DELETE_FLAG']:
            return Response(error_code.CONFLICT_4009(mode='DELETE', error_msg='deleted'),
                            status=status.HTTP_409_CONFLICT)
        else:
            # 'result/preprocessed_data'
            data_saved_path = PATH_CONFIG.PREPROCESSED_DATA
            # 'result/preprocess_transformer '
            transformer_saved_path = PATH_CONFIG.PREPROCESS_TRANSFORMER
            # 전처리된 데이터 저장 경로
            remove_file_path = os.path.join(data_saved_path, serializer.data['FILENAME'])
            # 데이터 전처리에 사용했던 기능들 저장 경로
            remove_transformer_list = glob.glob('{}/T_{}_*.pickle'. \
                                                format(transformer_saved_path, pk))
            if os.path.isfile(remove_file_path):
                os.remove(remove_file_path)  # 전처리된 데이터 파일 삭제
                for transformer_file in remove_transformer_list:  # 전처리에 사용된 전처리기 삭제
                    os.remove(transformer_file)
                serializer = PreprocessedDataSerializer(
                    preprocessed_data, data=dict(DELETE_FLAG=True), partial=True)
                if serializer.is_valid():
                    serializer.save()
                    return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                return Response(error_code.FILE_NOT_FOUND_4004(path_info=remove_file_path),
                                status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
def pfunction_download(request, pk):
    serializer = PreprocessedDataSerializer(
        get_object_or_404(PreprocessedData, pk=pk))
    if serializer.data['DELETE_FLAG']:
        return Response(error_code.CONFLICT_4009(mode='DELETE', error_msg='deleted'),
                        status=status.HTTP_409_CONFLICT)
    transformer_saved_path = PATH_CONFIG.PREPROCESS_TRANSFORMER
    # 데이터 전처리에 사용했던 기능들 저장 경로
    pk_savad_list = glob.glob('{}/T_{}_*.pickle'.format(transformer_saved_path, pk))
    response = HttpResponse(content_type='application/octet-stream')
    zip_file = zipfile.ZipFile(response, 'w', compression=zipfile.ZIP_DEFLATED)
    for file in pk_savad_list:
        zip_file.write(file, os.path.basename(file))
    zip_file.close()
    response['Content-Disposition'] = 'attachment; filename="T_{}.zip"'.format(pk)
    return response
