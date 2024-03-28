import time
from collections import defaultdict
from datetime import timedelta
from multiprocessing.pool import ThreadPool
from typing import DefaultDict, Dict, Generator, List, Set, Tuple

from cronutils.error_handler import ErrorHandler
from django.core.exceptions import ValidationError
from django.utils import timezone

from config.settings import CONCURRENT_NETWORK_OPS, FILE_PROCESS_PAGE_SIZE
from constants import common_constants
from constants.data_stream_constants import SURVEY_DATA_FILES
from database.data_access_models import ChunkRegistry, FileToProcess
from database.user_models_participant import Participant
from libs.file_processing.batched_network_operations import batch_upload
from libs.file_processing.csv_merger import CsvMerger
from libs.file_processing.data_qty_stats import calculate_data_quantity_stats
from libs.file_processing.file_for_processing import FileForProcessing
from libs.file_processing.utility_functions_simple import (BadTimecodeError, binify_from_timecode,
    clean_java_timecode, resolve_survey_id_from_file_name)
from libs.sentry import make_error_sentry, SentryTypes


def easy_run(participant: Participant):
    """ Just a handy way to just run data processing in the terminal, use with caution, does not
    test for celery activity. """
    print(f"processing files for {participant.patient_id}")
    processor = FileProcessingTracker(participant)
    processor.process_user_file_chunks()
    

"""########################## Hourly Update Tasks ###########################"""

# This is useful for testing and profiling behavior. Replaces the imported threadpool with this
# dummy class and poof! Single-threaded so the "threaded" network operations have real stack traces!
# class ThreadPool():
#     def map(self, *args, **kwargs):
#         # cut off any threadpool kwargs, which is conveniently easy because map does not use kwargs!
#         return map(*args)
#     def terminate(self): pass
#     def close(self): pass
#     def __init__(self, *args,**kwargs): pass

class FileProcessingTracker():
    def __init__(
        self, participant: Participant, page_size: int = FILE_PROCESS_PAGE_SIZE,
    ) -> None:
        self.error_handler: ErrorHandler = make_error_sentry(
            sentry_type=SentryTypes.data_processing, tags={'patient_id': participant.patient_id}
        )
        self.participant = participant
        self.study_id = participant.study.object_id
        self.patient_id = participant.patient_id
        
        # we operate on a page of files at a time, this is the size of the page.
        self.page_size = page_size
        
        # It is possible for devices to record data from unreasonable times, like the unix epoch
        # start. This huristic is a safety measure to clear out bad data.
        common_constants.LATEST_POSSIBLE_DATA_TIMESTAMP = \
            int(time.mktime((timezone.now() + timedelta(days=90)).timetuple()))
        
        # a defaultdict of a tuple of 2 lists - this stores the data that is being processed.
        self.all_binified_data: Dict[int, Tuple[List[bytes], List[bytes]]] = defaultdict(lambda: ([], []))
        
        # a dict to store the survey id from the file name, this is a very old design decision and
        # it is bad.
        self.survey_id_dict = {}
        
        # we don't actually use this...
        self.buggy_files = set()
        
        # old shit
        # self.position = 0
    
    #
    ## Outer Loop
    #
    
    def process_user_file_chunks(self):
        """ Call this function to process data for a participant. """
        for page_of_ftps in self.get_paginated_files_to_process():
            print(f"will process {len(page_of_ftps)} files.")
            self.do_process_user_file_chunks(page_of_ftps)
            self.survey_id_dict = {}
            self.buggy_files = set()
    
    def get_paginated_files_to_process(self) -> Generator[List[FileToProcess], None, None]:
        # we want to be able to delete database objects at any time so we get the whole contents of
        # the query. The memory overhead is not very high, if it ever is change this to a query for
        # pks and then each pagination is a separate query. (only memory overhead matters.)
        
        # sorting by s3_file_path clumps together the data streams, which is good for efficiency.
        pks = list(
            self.participant.files_to_process.exclude(deleted=True).order_by("s3_file_path")
        )
        print("Number Files To Process:", len(pks))
        
        # yield 100 files at a time
        ret = []
        for pk in pks:
            ret.append(pk)
            if len(ret) == self.page_size:
                yield ret  
                ret = []
        yield ret
    
    def do_process_user_file_chunks(self, files_to_process: List[FileToProcess]):
        """ Run through the files to process, pull their data, sort data into time bins. Run the
        file through the appropriate logic path based on file type. """
        
        # we use a ThreadPool to downloading multiple files simultaneously.
        pool = ThreadPool(CONCURRENT_NETWORK_OPS)
        
        # This pool pulls in data for each FileForProcessing on a background thread and instantiates it.
        # Instantiating a FileForProcessing object queries S3 for the File's data. (network request)
        files_for_processing: List[FileForProcessing] = pool.map(
            FileForProcessing, files_to_process, chunksize=1
        )
        
        for file_for_processing in files_for_processing:
            with self.error_handler:
                self.process_one_file(file_for_processing)
        pool.close()
        pool.terminate()
        
        # there are several failure modes and success modes, information for what to do with different
        # files percolates back to here.  Delete various database objects accordingly.
        ftps_to_remove, bad_files, earliest_time_bin, latest_time_bin = self.upload_binified_data()
        self.buggy_files.update(bad_files)
        print(f"Successully processed {len(ftps_to_remove)} files, there have been a total of {len(self.buggy_files)} failed files.")
        
        # Update the data quantity stats (if it actually processed any files)
        if len(files_to_process) > 0:
            calculate_data_quantity_stats(self.participant, earliest_time_bin, latest_time_bin)
        
        # Actually delete the processed FTPs from the database now that we are done.
        FileToProcess.objects.filter(pk__in=ftps_to_remove).delete()
        
    def process_one_file(self, file_for_processing: FileForProcessing):
        """ Dispatches a file to the correct processing logic. """
        if file_for_processing.exception:
            file_for_processing.raise_data_processing_error()
        
        # there are two cases: chunkable data that can be stuck into "time bins" for each hour, and
        # files that do not need to be "binified" and pretty much just go into the ChunkRegistry unmodified.
        if file_for_processing.chunkable:
            self.process_chunkable_file(file_for_processing)
        else:
            self.process_unchunkable_file(file_for_processing)
    
    def upload_binified_data(self) -> Tuple[Set[int], List[int], int, int]:
        """ Takes in binified csv data and handles uploading/downloading+updating
            older data to/from S3 for each chunk.
            
            Returns a set of FTPs that have succeeded and can be removed.
            Returns a list of FTPs that failed.
            Returns the earliest and latest time bins handled. """
        # Track the earliest and latest time bins, to return them at the end of the function
        uploads = CsvMerger(
            self.all_binified_data, self.error_handler, self.survey_id_dict, self.participant
        )
        
        pool = ThreadPool(CONCURRENT_NETWORK_OPS)
        errors = pool.map(batch_upload, uploads.upload_these, chunksize=1)
        
        # this code predates the refactor into a class... it seems real bad. Maybe it is supposed to
        # be within the error handler but that caused error spam/sentry quota depletion?
        for err_ret in errors:
            if err_ret['exception']:
                print(err_ret['traceback'])
                raise err_ret['exception']
        
        pool.close()
        pool.terminate()
        return uploads.get_retirees()
    
    #
    ## Chunkable File Processing
    #
    
    def process_chunkable_file(self, file_for_processing: FileForProcessing):
        """ logic for downloading, fixing, merging, but not uploading data from one file. """
        newly_binified_data, survey_id_hash = self.process_csv_data(file_for_processing)
        
        # survey answers store the survey id in the file name (truly ancient design decision, its
        # bad and buggy, need to get around to fixing this).
        if file_for_processing.data_type in SURVEY_DATA_FILES:
            self.survey_id_dict[survey_id_hash] = resolve_survey_id_from_file_name(
                file_for_processing.file_to_process.s3_file_path
            )
        
        if newly_binified_data:
            self.append_binified_csvs(newly_binified_data, file_for_processing.file_to_process)
        else:
            # delete empty files from FilesToProcess
            file_for_processing.file_to_process.delete()
    
    def append_binified_csvs(
            self,
            new_binified_rows: DefaultDict[tuple, list],
            file_for_processing:  FileToProcess
        ):
        """ Appends new binified rows to existing binified row data structure, in-place. """
        for data_bin, rows in new_binified_rows.items():
            self.all_binified_data[data_bin][0].extend(rows)  # Add data rows
            self.all_binified_data[data_bin][1].append(file_for_processing.pk)  # Add ftp
        return
    
    def process_csv_data(self, file_for_processing: FileForProcessing) -> Tuple[DefaultDict[tuple, list], Tuple[str, str, str, bytes]]:
        """ Constructs a binified dict of a given list of a csv rows, catches csv files with known
            problems and runs the correct logic. Returns None If the csv has no data in it. """
        # long running function. decomposes the file into a list of rows and a header, applies data
        # stream fixes.
        file_for_processing.prepare_data()
        # get the header and rows from the file, tell it to clear references to the file contents
        csv_rows_list = file_for_processing.file_lines
        header = file_for_processing.header
        file_for_processing.clear_file_lines()
        
        # shove csv rows into their respective time bins
        # upon returning from this function there should only be the binified data representation in memory
        if csv_rows_list:
            return (
                # return item 1: the data as a defaultdict
                self.binify_csv_rows(csv_rows_list, file_for_processing.data_type, header),
                # return item 2: the tuple that we use as a key for the defaultdict
                (self.study_id, self.patient_id, file_for_processing.data_type, header)
            )
        else:
            return None, None
    
    def binify_csv_rows(self, rows_list: list, data_type: str, header: bytes) -> DefaultDict[tuple, list]:
        """ Assumes a clean csv with element 0 in the row's column as a unix(ish) timestamp.
            Sorts data points into the appropriate bin based on the rounded down hour
            value of the entry's unix(ish) timestamp. (based CHUNK_TIMESLICE_QUANTUM)
            Returns a dict of form {(study_id, patient_id, data_type, time_bin, header):rows_lists}. """
        ret = defaultdict(list)
        for row in rows_list:
            # discovered August 7 2017, looks like there was an empty line at the end
            # of a file? row was a [''].
            if row and row[0]:
                # this is the first thing that will hit corrupted timecode values errors (origin of which is unknown).
                try:
                    timecode = binify_from_timecode(row[0])
                except BadTimecodeError:
                    continue
                ret[(self.study_id, self.patient_id, data_type, timecode, header)].append(row)
        return ret
    
    #
    ## Unchunkable File Processing
    #
    
    def process_unchunkable_file(self, file_for_processing: FileForProcessing):
        """ Processes a file that is not chunkable. Registers it in the ChunkRegistry directly."""
        try:
            # if the timecode is bad, we scrap this file. We just don't care.
            timestamp = clean_java_timecode(
                file_for_processing.file_to_process.s3_file_path.rsplit("/", 1)[-1][:-4]
            )
        except BadTimecodeError:
            file_for_processing.file_to_process.delete()
            return
        
        # Since we aren't binning the data by hour, just create a ChunkRegistry that
        # points to the already existing S3 file.
        try:
            ChunkRegistry.register_unchunked_data(
                file_for_processing.data_type,
                timestamp,
                file_for_processing.file_to_process.s3_file_path,
                file_for_processing.file_to_process.study.pk,
                file_for_processing.file_to_process.participant.pk,
                file_for_processing.file_contents,
            )
            file_for_processing.file_to_process.delete()
        except ValidationError as ve:
            if len(ve.messages) != 1:
                # case: the error case (below) is very specific, we only want that singular error.
                raise
            
            # case: an unchunkable file was re-uploaded, causing a duplicate file path collision
            # we detect this specific case and update the registry with the new file size
            # (hopefully it doesn't actually change)
            if 'Chunk registry with this Chunk path already exists.' in ve.messages:
                ChunkRegistry.update_registered_unchunked_data(
                    file_for_processing.data_type,
                    file_for_processing.file_to_process.s3_file_path,
                    file_for_processing.file_contents,
                )
                file_for_processing.file_to_process.delete()
            else:
                # any other errors, add
                raise
        return
