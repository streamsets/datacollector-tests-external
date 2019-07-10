# Copyright 2019 StreamSets Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import string
import tempfile

from streamsets.testframework.markers import sftp, sdc_min_version, ssh
from streamsets.testframework.utils import get_random_string

logger = logging.getLogger(__name__)


@sdc_min_version('3.8.0')
@sftp
def test_sftp_origin(sdc_builder, sdc_executor, sftp):
    """Smoke test SFTP origin. We first create a file on SFTP server and have the SFTP origin stage read it.
    We then assert its snapshot. The pipeline look like:
        sftp_ftp_client >> trash
    """
    sftp_file_name = get_random_string(string.ascii_letters, 10)
    raw_text_data = 'Hello World!'
    sftp.put_string(os.path.join(sftp.path, sftp_file_name), raw_text_data)

    builder = sdc_builder.get_pipeline_builder()
    sftp_ftp_client = builder.add_stage(name='com_streamsets_pipeline_stage_origin_remote_RemoteDownloadDSource')
    sftp_ftp_client.file_name_pattern = sftp_file_name
    sftp_ftp_client.data_format = 'TEXT'

    trash = builder.add_stage('Trash')

    sftp_ftp_client >> trash
    sftp_ftp_client_pipeline = builder.build('SFTP Origin Pipeline').configure_for_environment(sftp)
    sdc_executor.add_pipeline(sftp_ftp_client_pipeline)

    snapshot = sdc_executor.capture_snapshot(sftp_ftp_client_pipeline, start_pipeline=True).snapshot
    sdc_executor.stop_pipeline(sftp_ftp_client_pipeline)

    assert len(snapshot[sftp_ftp_client].output) == 1
    assert snapshot[sftp_ftp_client].output[0].field['text'] == raw_text_data

    # Delete the test SFTP origin file we created
    transport, client = sftp.client
    try:
        client.remove(os.path.join(sftp.path, sftp_file_name))
    finally:
        client.close()
        transport.close()


@sdc_min_version('3.8.0')
@sftp
@ssh
def test_sftp_origin_open_files(sdc_builder, sdc_executor, sftp, ssh):
    """Test SFTP origin to see if it leaves any open files on the SSH server after its pipeline has processed records.
    We first create a file on SFTP server and have the SFTP origin stage read it. We then check if open files are left
    after the pipeline processes all data. The pipeline look like:
        sftp_ftp_client >> trash
    """
    sftp_file_name = get_random_string(string.ascii_letters, 10)
    raw_text_data = 'Hello World!'
    logger.debug('Creating file at %s/%s on SFTP server ...', sftp.path, sftp_file_name)
    sftp.put_string(os.path.join(sftp.path, sftp_file_name), raw_text_data)

    builder = sdc_builder.get_pipeline_builder()
    sftp_ftp_client = builder.add_stage(name='com_streamsets_pipeline_stage_origin_remote_RemoteDownloadDSource')
    sftp_ftp_client.file_name_pattern = sftp_file_name
    sftp_ftp_client.data_format = 'TEXT'

    trash = builder.add_stage('Trash')

    sftp_ftp_client >> trash
    sftp_ftp_client_pipeline = builder.build('SFTP Origin open file check Pipeline').configure_for_environment(sftp)
    sdc_executor.add_pipeline(sftp_ftp_client_pipeline)
    start_command = sdc_executor.start_pipeline(sftp_ftp_client_pipeline)

    ssh_client = ssh.client
    try:
        # make sure pipeline has processed all data
        start_command.wait_for_pipeline_output_records_count(1)
        # since now pipeline has processed all records, make sure it has not left a open stream to the remote file
        ssh_stdin, ssh_stdout, ssh_stderr = ssh_client.exec_command(f'lsof | grep {sftp_file_name}')
        lsof_status = ssh_stdout.channel.recv_exit_status()
        assert lsof_status == 1
    finally:
        sdc_executor.stop_pipeline(sftp_ftp_client_pipeline)
        ssh_client.close()
        # Delete the test SFTP origin file we created
        transport, sftp_client = sftp.client
        try:
            logger.debug('Removing file at %s/%s on SFTP server ...', sftp.path, sftp_file_name)
            sftp_client.remove(os.path.join(sftp.path, sftp_file_name))
        finally:
            sftp_client.close()
            transport.close()


@sdc_min_version('3.9.0')
@sftp
def test_sftp_destination(sdc_builder, sdc_executor, sftp):
    """Smoke test SFTP destination. We first create a local file using Local FS destination stage and use that file
    for SFTP destination stage to see if it gets successfully uploaded.
    The pipelines look like:
        dev_raw_data_source >> local_fs
        directory >> sftp_ftp_client
    """
    # Our destination SFTP file name
    sftp_file_name = get_random_string(string.ascii_letters, 10)
    # Local temporary directory where we will create a source file to be uploaded to SFTP server
    local_tmp_directory = os.path.join(tempfile.gettempdir(), get_random_string(string.ascii_letters, 10))

    # Build source file pipeline logic
    builder = sdc_builder.get_pipeline_builder()

    dev_raw_data_source = builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.data_format = 'TEXT'
    dev_raw_data_source.raw_data = 'Hello World!'
    dev_raw_data_source.stop_after_first_batch = True

    local_fs = builder.add_stage('Local FS', type='destination')
    local_fs.directory_template = local_tmp_directory
    local_fs.data_format = 'TEXT'

    dev_raw_data_source >> local_fs
    local_fs_pipeline = builder.build('Local FS Pipeline')

    builder = sdc_builder.get_pipeline_builder()

    # Build SFTP destination pipeline logic
    directory = builder.add_stage('Directory', type='origin')
    directory.data_format = 'WHOLE_FILE'
    directory.file_name_pattern = 'sdc*'
    directory.files_directory = local_tmp_directory

    sftp_ftp_client = builder.add_stage(name='com_streamsets_pipeline_stage_destination_remote_RemoteUploadDTarget')
    sftp_ftp_client.file_name_expression = sftp_file_name

    directory >> sftp_ftp_client
    sftp_ftp_client_pipeline = builder.build('SFTP Destination Pipeline').configure_for_environment(sftp)

    sdc_executor.add_pipeline(local_fs_pipeline, sftp_ftp_client_pipeline)

    # Start source file creation pipeline and assert file has been created with expected number of records
    sdc_executor.start_pipeline(local_fs_pipeline).wait_for_finished()
    history = sdc_executor.get_pipeline_history(local_fs_pipeline)
    assert history.latest.metrics.counter('pipeline.batchInputRecords.counter').count == 1
    assert history.latest.metrics.counter('pipeline.batchOutputRecords.counter').count == 1

    # Start SFTP upload (destination) file pipeline and assert pipeline has processed expected number of files
    sdc_executor.start_pipeline(sftp_ftp_client_pipeline).wait_for_pipeline_output_records_count(1)
    sdc_executor.stop_pipeline(sftp_ftp_client_pipeline)
    history = sdc_executor.get_pipeline_history(sftp_ftp_client_pipeline)
    assert history.latest.metrics.counter('pipeline.batchInputRecords.counter').count == 1
    assert history.latest.metrics.counter('pipeline.batchOutputRecords.counter').count == 1

    # Read SFTP destination file and compare our source data to assert
    assert sftp.get_string(os.path.join(sftp.path, sftp_file_name)).strip() == dev_raw_data_source.raw_data

    # Delete the test SFTP origin file we created
    transport, client = sftp.client
    try:
        client.remove(os.path.join(sftp.path, sftp_file_name))
    finally:
        client.close()
        transport.close()
