from datetime import datetime
from typing import Generator, List, Tuple

from constants.common_constants import API_TIME_FORMAT


def insert_timestamp_single_row_csv(header: bytes, rows_list: List[list], time_stamp: bytes) -> bytes:
    """ Inserts the timestamp field into the header of a csv, inserts the timestamp
        value provided into the first column.  Returns the new header string."""
    header_list = header.split(b",")
    header_list.insert(0, b"timestamp")
    rows_list[0].insert(0, time_stamp)
    return b",".join(header_list)


def csv_to_list(file_contents: bytes) -> Tuple[bytes, List[bytes]]:
    """ Grab a list elements from of every line in the csv, strips off trailing whitespace. dumps
    them into a new list (of lists), and returns the header line along with the list of rows. """
    
    # This code is more memory efficient than fast by using a generator
    # Note that almost all of the time is spent in the per-row for-loop
    
    # case: the file coming in is just a single line, e.g. the header.
    # Need to provide the header and an empty iterator.
    if b"\n" not in file_contents:
        return file_contents, []
    
    lines = file_contents.splitlines()
    return lines.pop(0), list(line.split(b",") for line in lines)
    
# We used to have a generator version of this function that nominally has better memory usage, but
# it was slower than just doing the splitlines, and caused problems with fixes after the last
# section of the file processing refactor.
#     line_iterator = isplit(file_contents)
#     header = b",".join(next(line_iterator))
#     header2 = file_contents[:file_contents.find(b"\n")]
#     assert header2 == header, f"\n{header}\n{header2}"
#     return header, line_iterator
# def isplit(source: bytes) -> Generator[List[bytes], None, None]:
#     """ Generator version of str.split()/bytes.split() """
#     # version using str.find(), less overhead than re.finditer()
#     start = 0
#     while True:
#         # find first split
#         idx = source.find(b"\n", start)
#         if idx == -1:
#             yield source[start:].split(b",")
#             return
#         yield source[start:idx].split(b",")
#         start = idx + 1
# def isplit2(source: bytes) -> Generator[bytes, None, None]:
#     """ This is actually faster, almost as fast as the splitlines method, but it returns items in reverse order... """
#     lines = source.splitlines()
#     del source
#     yield lines.pop(0)
#     while lines:
#         yield lines.pop(-1).split(b",")

def construct_csv_string(header: bytes, rows_list: List[bytes]) -> bytes:
    """ Takes a header list and a bytes-list and returns a single string of a csv. Very performant."""
    
    def deduplicate(seq: List[bytes]):
        # highly optimized order preserving deduplication function.
        seen = set()
        seen_add = seen.add
        # list comprehension is slightly slower, tuple() is faster for smaller counts, list()
        #  is very slightly faster on large counts.  tuple() *should* have lower memory overhead?
        return tuple(x for x in seq if not (x in seen or seen_add(x)))
    
    # this comprehension is always fastest, there is no advantage to inlining the creation of rows
    rows = [b",".join(row_items) for row_items in rows_list]
    # we need to ensure no duplicates
    rows = deduplicate(rows)
    
    # the .join is at least 100x faster than a +=ing a ret string - I don't know how it made it
    # as long as it did as a += operation, I knew that was slow because of repeated calls to alloc.
    return header + b"\n" + b"\n".join(rows)


def unix_time_to_string(unix_time: int) -> bytes:
    return datetime.utcfromtimestamp(unix_time).strftime(API_TIME_FORMAT).encode()
