#!/usr/bin/env python
# (c) Stefan Countryman, 2017

DESC = """Find all frame files of a given frame type in a given time range on
a remote LIGO server and download them to the specified output directory,
skipping any files that have already been downloaded."""
PROG_BAR_WIDTH = 40
VERBOSE = True  # default to verbose for interactive sessions
DEFAULT_H_FRAMETYPES = []
DEFAULT_L_FRAMETYPES = []
DEFAULT_V_FRAMETYPES = []
DEFAULT_FRAME_LENGTH = 64
DEFAULT_SERVER = 'ldas-pcdev2.ligo.caltech.edu'
DEFAULT_OUTDIR = '.'
_TARGETED_SEARCH_FRAMETYPE_DICT_CIT = {
    "H1_HOFT_C02":  "hoft_C02/H1",
    "L1_HOFT_C02":  "hoft_C02/L1",
    "H1_R":         "raw/H1",
    "L1_R":         "raw/L1"
}
_TARGETED_SEARCH_SERVERS_CIT = ["ldas-pcdev{}.ligo.caltech.edu".format(i)
                                for i in range(1,6)]
# map tuples of (observing run, frametype, server) to directories
# containing frames on the remote server for fallback frame searches.
_TARGETED_SEARCH_DIRECTORIES = dict()
_TARGETED_SEARCH_DIR_FMT_CIT = "/hdfs/frames/{}/{}"
for run in ["O1", "O2"]:
    for server in _TARGETED_SEARCH_SERVERS_CIT:
        for frametype in _TARGETED_SEARCH_FRAMETYPE_DICT_CIT:
            key = (run, frametype, server)
            path = _TARGETED_SEARCH_DIR_FMT_CIT.format(
                run,
                _TARGETED_SEARCH_FRAMETYPE_DICT_CIT[frametype]
            )
            _TARGETED_SEARCH_DIRECTORIES[key] = path
_BLUE = '\033[94m'
_CLEAR = '\033[0m'
_COMPLAINT = "{}---[{{}}]---{}\n{{}}\n".format(_BLUE, _CLEAR)

# all other imports listed after argument parsing, allowing for fast help
# documentation printing.
import sys
from subprocess import Popen, PIPE
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=DESC)
    parser.add_argument(
        "-T",
        "--times",
        action="store_true",
        help="""
            If provided, {} will read a list of (start, stop) times from STDIN
            and will attempt to download frames for these time intervals
            (instead of time intervals specified by the ``--start`` and
            ``--deltat`` arguments, which are ignored if ``--times`` is
            specified).
        """.format(sys.argv[0])
    )
    parser.add_argument(
        "-r",
        "--retries",
        type=int,
        default=0,
        help="""
            In the event of an error, retry an additional ``retries`` number of
            times. By default, do not retry (i.e. ``retries = 0``). To keep
            retrying an infinite number of times, specify any negative number.
        """
    )
    parser.add_argument(
        "-t",
        "--start",
        type=int,
        help="""
            The starting GPS time for this dump. Will be rounded down to the
            nearest even multiple of ``--length``, since the frame files all
            start on multiples of ``--length`` seconds GPS time.
        """
    )
    parser.add_argument(
        "-l",
        "--length",
        default=DEFAULT_FRAME_LENGTH,
        type=int,
        help="""
            The duration of each frame file. The script is dumb and will only
            try to find files assuming this length. DEFAULT: {}
        """.format(DEFAULT_FRAME_LENGTH)
    )
    parser.add_argument(
        "-p",
        "--progress",
        action="store_true",
        help="""
            Don't bother downloading; instead, just try to show how much
            progress has been made on this job based on the files it finds.
        """
    )
    parser.add_argument(
        "-d",
        "--deltat",
        type=int,
        help="""
            The length of the time window in which to seek frame files,
            measured in seconds. For example, 86400 seconds is a whole day.
        """
    )
    parser.add_argument(
        "-s",
        "--server",
        default=DEFAULT_SERVER,
        help="""
            The URL of the server on which to perform a ``gw_data_find`` query
            followed by a frame file download. DEFAULT: {}
            """.format(DEFAULT_SERVER)
    )
    parser.add_argument(
        "-o",
        "--outdir",
        default=DEFAULT_OUTDIR,
        help="""
            The default directory in which to save downloaded frame files.
            DEFAULT: {}
            """.format(DEFAULT_OUTDIR)
    )
    parser.add_argument(
        "-H",
        "--hanford-frametypes",
        nargs="*",
        default=DEFAULT_H_FRAMETYPES,
        help="""
            The frametypes to download for the Hanford detector (LHO). These
            can be things like raw files (H1_R) or increasingly calibrated
            strain files (H1_HOFT_C00, H1_HOFT_C01, or H1_HOFT_C02). DEFAULT:
            {}
            """.format(DEFAULT_H_FRAMETYPES)
    )
    parser.add_argument(
        "-L",
        "--livingston-frametypes",
        nargs="*",
        default=DEFAULT_L_FRAMETYPES,
        help="""
            The frametypes to download for the Livingston detector (LLO). These
            can be things like raw files (L1_R) or increasingly calibrated
            strain files (L1_HOFT_C00, L1_HOFT_C01, or L1_HOFT_C02). DEFAULT:
            {}
            """.format(DEFAULT_L_FRAMETYPES)
    )
    parser.add_argument(
        "-V",
        "--virgo-frametypes",
        nargs="*",
        default=DEFAULT_V_FRAMETYPES,
        help="""
            The frametypes to download for Virgo. DEFAULT:
            {}
            """.format(DEFAULT_V_FRAMETYPES)
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print diagnostic information to STDERR."
    )
    args = parser.parse_args()
    VERBOSE = args.verbose
if VERBOSE:  # if verbose, print everything
    _PIPE_ARGS = {"stdout": PIPE}
else:
    _PIPE_ARGS = {"stdout": PIPE, "stderr": PIPE}

import filecmp
import numpy as np
import collections
from datetime import datetime
import time
import os


class GWDataFindException(Exception):
    """An error thrown when ``gw_data_find`` on the remote server fails."""


class GWDataDownloadException(Exception):
    """An error thrown when ``gsiscp`` fails to download the desired data."""


class GWRemoteSha256Exception(Exception):
    """An error thrown when ``sha256sum`` fails on the remote server."""


class GWLocalSha256Exception(Exception):
    """An error thrown when ``sha256sum`` fails locally."""


class FileNameParsingError(Exception):
    """An error thrown when a filename that must be parsed does not follow the
    expected format. The message should contain some information about the
    unparsable filename to aid in debugging."""


class TargetedSearchException(Exception):
    """An exception raised when a targeted search cannot be performed due to a
    lack of target directories."""


def time_since_file_modified(filename):
    """Get the elapsed time in seconds since a file was modified."""
    return time.time() - os.path.getmtime(filename)


def complain(*messages):
    """Write a message to stderr if running in interactive mode or if the
    ``--verbose`` flag is set. Otherwise, throw away the message. If multiple
    messages are provided, join them with newlines."""
    msg = '\n'.join([format(m) for m in messages])
    if VERBOSE:
        formatted_message = _COMPLAINT.format(datetime.now().isoformat(), msg)
        sys.stderr.write(formatted_message)


class RemoteFileInfo(object):
    """A container holding data about a remote frame file (based on its
    filename as returned by ``gw_data_find``) along with convenience methods
    for generating a proper local file name.
    
    It is assumed that URLs returned by ``gw_data_find`` look like:
    
    file://localhost/hdfs/frames/O2/hoft_C02/H1/H-H1_HOFT_C02-11869/H-H1_HOFT_C02-1186959360-4096.gwf

    """

    def __init__(self, gw_data_find_response):
        """Initialize using the ``gw_data_find`` file URL string. Strips
        surrounding whitespace from the remote filename."""
        self.gw_data_find_response = gw_data_find_response.strip()

    FILE_URL_PREFIX = "file://localhost"

    @property
    def fullpath(self):
        """Return the full path on the remote server (removing the
        "file://localhost" prefix from the response string)."""
        return self.gw_data_find_response.replace(self.FILE_URL_PREFIX, '')

    @property
    def filename(self):
        """Get the remote filename without the containing directory."""
        return os.path.basename(self.fullpath)

    @property
    def gps_start_time(self):
        """Get the GPS start time of this frame file."""
        try:
            return self.filename.split('.')[0].split('-')[2]
        except IndexError as e:
            msg = 'Cannot get GPS start time from filename: ' + self.filename
            complain(msg)
            raise FileNameParsingError(msg)

    @property
    def frame_duration(self):
        """Get the duration in seconds of this frame file."""
        try:
            return self.filename.split('.')[0].split('-')[3]
        except IndexError as e:
            msg = 'Cannot get frame duration from filename: ' + self.filename
            complain(msg)
            raise FileNameParsingError(msg)


class GWFrameQuery(object):
    """An object specifying the detector, frametype, frame start time, output
    directory, and server where remote data is stored for a GW frame.  Used to
    check if the frame file exists, and, if it doesn't, to find the file on a
    remote server and download it."""

    def __init__(self, detector, frametype, gpstime,
                 framelength=DEFAULT_FRAME_LENGTH, server=DEFAULT_SERVER,
                 outdir=DEFAULT_OUTDIR):
        self.detector = detector
        self.frametype = frametype
        self.gpstime = gpstime
        self.framelength = framelength
        self.server = server
        self.outdir = outdir

    _FILENAME_FORMAT = '{}-{}-{}-{}.gwf'

    _GW_DATA_FIND_QUERY_FMT = """gw_data_find \\
        --observatory {} \\
        --type {} \\
        --gps-start-time {} \\
        --gps-end-time {} \\
        --url-type file
    """

    _TARGETED_SEARCH_QUERY_FMT = "find {} -name {}"

    _TARGETED_SEARCH_DIRECTORIES = _TARGETED_SEARCH_DIRECTORIES

    _OBSERVING_RUNS = {
        "ER8": (1126051217, 1126627217),
        "O1": (1126627217, 1137258496),
        "O2": (1164556817, 1187733618)
    }

    _SHA256_SUM_FMT = "sha256sum '{}'"

    def execute_cmd_over_ssh(self, cmd, exception=Exception):
        """SSH into this Query's server and run ``cmd``, capturing and
        returning a tuple containing (stdout, stderr) and throwing the
        specified ``exception`` type in the event of a nonzero return code."""
        fullcmd = ['gsissh', self.server, cmd]
        proc = Popen(fullcmd, stdout=PIPE, stderr=PIPE)
        res, err = proc.communicate()
        complain("RETVAL:", proc.returncode, "STDOUT:", res, "STDERR:", err)
        if proc.returncode != 0:
            raise exception("Something went wrong: {}".format(err))
        return (res, err)

    @property
    def observing_run(self):
        """Get the observing run for this query by looking at the time window
        it belonged to. Returns ``None`` if no matching run is found."""
        for run in self._OBSERVING_RUNS:
            interval = self._OBSERVING_RUNS[run]
            if (interval[0] < self.gpstime) and (self.gpstime < interval[1]):
                return run
        return None

    @property
    def estimated_filename(self):
        """What the filename *should* be assuming that the frame length of
        each frame file on the *remote server* is the same as the
        ``framelength`` specified in this query object."""
        return self._FILENAME_FORMAT.format(
            self.detector,
            self.frametype,
            self.gpstime,
            self.framelength
        )

    @property
    def estimated_fullpath(self):
        """Again, what the full path *should* be. See
        ``estimated_filename``."""
        return os.path.join(self.outdir, self.estimated_filename)

    def estimated_fullpath_exists(self):
        """Has this frame file already been downloaded? Do a naive check for
        what we *think* the filename is, though this might actually be
        different if the frames on the remote server have different frame
        lengths than what we estimated for this query in ``framelength``."""
        return os.path.isfile(self.estimated_fullpath)

    def local_filename_from_remote(self, remote_url):
        """Get the local filename based on the filename of the remote URL. This
        might not be the expected filename, so we need to check."""
        remote_file_info = RemoteFileInfo(remote_url)
        return self._FILENAME_FORMAT.format(
            self.detector,
            self.frametype,
            remote_file_info.gps_start_time,
            remote_file_info.frame_duration
        )

    def local_fullpath_from_remote(self, remote_url):
        """Get the local full path based on the filename of the remote URL.
        This might not be the expected full path, so we need to check."""
        return os.path.join(
            self.outdir,
            self.local_filename_from_remote(remote_url)
        )

    LOCAL_RIDER_TYPES = [
        'remote_sha256',
        'local_sha256',
        'query_repr',
        'error_msg',
        'remote_url'
    ]
    RIDER_FORMAT = '.{}.{}.txt'
    LocalRiders = collections.namedtuple('LocalRiders', LOCAL_RIDER_TYPES)

    def estimated_rider_fullpaths(self):
        """Get a ``LocalRiders`` namedtuple specifying the file paths to
        rider files for this query. These files contain metadata about the
        remote download, specifically, the remote and local sha256 sums, the
        file query used to generate the file, and the remote_url originally
        returned by gw_data_find. This is based on the *expected* filename, not
        the actual filename as determined by ``gw_data_find``."""
        filename = self.estimated_filename
        rider_filenames = [
            self.RIDER_FORMAT.format(
                filename,
                rider_type
            ) for rider_type in self.LOCAL_RIDER_TYPES
        ]
        rider_fullpaths = [
            os.path.join(self.outdir, fname) for fname in rider_filenames
        ]
        return self.LocalRiders(*rider_fullpaths)

    def local_rider_fullpaths_from_remote(self, remote_url):
        actual_local_filename = self.local_filename_from_remote(remote_url)
        rider_filenames = [
            self.RIDER_FORMAT.format(
                actual_local_filename,
                rider_type
            ) for rider_type in self.LOCAL_RIDER_TYPES
        ]
        rider_fullpaths = [
            os.path.join(self.outdir, fname) for fname in rider_filenames
        ]
        return self.LocalRiders(*rider_fullpaths)

    def local_fullpath_from_remote_exists(self, remote_url):
        """Check whether the local file corresponding to the remote_url exists.
        The remote_url might have an unexpected filename due to e.g. differing
        frame durations, so we need to check this."""
        return os.path.isfile(self.local_fullpath_from_remote(remote_url))

    def remote_url_targeted_search(self):
        """If the remote_url search fails, we can try a targetted search
        instead for certain frame types and time periods."""
        run = self.observing_run
        if run == None:
            raise TargetedSearchException("Cannot determine run.")
        key = (run, self.frametype, self.server)
        try:
            searchdir = self._TARGETED_SEARCH_DIRECTORIES[key]
        except KeyError:
            raise TargetedSearchException("No search target dir defined.")
        query = self._TARGETED_SEARCH_QUERY_FMT.format(
            searchdir,
            self.estimated_filename
        )
        res, err = self.execute_cmd_over_ssh(query, TargetedSearchException)
        try:
            return res.split('\n')[0].strip()
            complain("Targeted search found file for {}".format(self))
        except IndexError:
            raise TargetedSearchException("No remote file found.")

    def remote_url(self):
        """Get the path to this frame file on the remote server. First, try a
        search using ``gw_data_find``; if this fails, as often happens, do a
        manually-targetted search."""
        query = self._GW_DATA_FIND_QUERY_FMT.format(
            self.detector,
            self.frametype,
            self.gpstime,
            self.gpstime
        )
        res, err = self.execute_cmd_over_ssh(query, GWDataFindException)
        remote_url = res.strip().replace('file://localhost', '')
        if remote_url == '':
            msg = '{} not found using gw_data_find. Trying targetted search.'
            complain(msg.format(self))
            try:
                remote_url = self.remote_url_targeted_search()
            except TargetedSearchException as e:
                msg = 'Targeted search failed, returning "" for {}: {}'
                complain(msg.format(self, e))
                return ''
        return remote_url

    def remote_sha256(self, remote_url=None):
        """Get the sha256 sum for the file specified in ``self.remote_url()``
        and write it to a rider file (if the rider file does not already
        exist). Optionally override the ``remote_url`` argument, for example if
        the remote URL (as returned by ``gw_data_find``) has already been
        fetched."""
        # if no remote URL specified, find it automatically
        if remote_url == None:
            remote_url = self.remote_url()
        remote_fullpath = RemoteFileInfo(remote_url).fullpath
        sha256cmd = self._SHA256_SUM_FMT.format(remote_fullpath)
        try:
            res, err = self.execute_cmd_over_ssh(sha256cmd,
                                                 GWRemoteSha256Exception)
        except GWRemoteSha256Exception:
            errtime = datetime.utcnow().isoformat()
            errfmt = "REMOTE_SHA256 ERROR at {}. STDERR: \n{}\n"
            errmsg = errfmt.format(errtime, err)
            raise GWRemoteSha256Exception(errmsg)
        return res.split()[0]

    def local_sha256(self, remote_url=None):
        """Get the sha256 sum for the *local* file specified by
        ``self.remote_url()`` and write it to a rider file (if the rider file
        does not already exist). Optionally override the ``remote_url``
        argument, for example if the remote URL (as returned by
        ``gw_data_find``) has already been fetched."""
        # if no remote URL specified, find it automatically
        if remote_url == None:
            remote_url = self.remote_url()
        fullpath = self.local_fullpath_from_remote(remote_url)
        cmd = ['sha256sum', fullpath]
        complain("Running command in subprocess:", cmd)
        proc = Popen(cmd, **_PIPE_ARGS)
        res, err = proc.communicate()
        complain("RETVAL:", proc.returncode, "STDOUT:", res, "STDERR:", err)
        if proc.returncode != 0:
            errtime = datetime.utcnow().isoformat()
            errfmt = "LOCAL_SHA256 ERROR at {}. STDERR: \n{}\n"
            errmsg = errfmt.format(errtime, err)
            raise GWLocalSha256Exception(errmsg)
        return res.split()[0]

    def local_file_corrupted(self, remote_url=None):
        """Check whether the local downloaded file is corrupt, e.g. due to an
        interrupted download or disk corruption. Returns ``True`` if the file
        is corrupted, ``False`` otherwise. Figures out the local filename
        from the ``self.remote_url()``. Optionally, override this check by
        providing the remote URL returned by ``gw_data_find`` on the remote
        server (to save time if it has already been calculated)."""
        if remote_url == None:
            remote_url = self.remote_url()
        riders = self.local_rider_fullpaths_from_remote(remote_url)
        try:
            with open(riders.remote_sha256, 'r') as f:
                remote_sha256 = f.read()
            with open(riders.local_sha256, 'r') as f:
                local_sha256 = f.read()
            if local_sha256 != remote_sha256:
                return True
        except IOError:
            return True
        # if we made it to the end, checks have passed and the file is not
        # corrupt
        return False

    def download(self):
        """Download the file specified in ``self.remote_url()`` from the
        remote server. The remote file might actually have a different filename
        than what is expected, particularly if the user has incorrectly
        guessed the frame duration, so an extra check is made to see if the
        local filename differs. Also write the ``self.remote_url()`` to a rider
        file for future reference."""
        remote_url = self.remote_url()
        # we might not be able to parse the path if the remote file does not
        # exist. in this case, log the error and move on to the next file.
        try:
            local_fullpath = self.local_fullpath_from_remote(remote_url)
        except FileNameParsingError as e:
            estimated_riders = self.estimated_rider_fullpaths()
            with open(estimated_riders.error_msg, 'a') as f:
                f.write(e.args[0] + '\n')
            raise e
        riders = self.local_rider_fullpaths_from_remote(remote_url)
        # Check whether the file has been partially downloaded or is corrupt.
        # If it is corrupt, delete it so that it can be redownloaded.
        if os.path.isfile(local_fullpath):
            if self.local_file_corrupted(remote_url):
                #  check whether the file was modified in the last minute.  If
                #  so, then it might still be an active download and should be
                #  left alone.
                if time_since_file_modified(local_fullpath) > 60:
                    errtime = datetime.utcnow().isoformat()
                    errfmt = "CORRUPT FILE at {}. DELETING AND PROCEEDING.\n"
                    errmsg = errfmt.format(errtime)
                    with open(riders.error_msg, 'a') as f:
                        f.write(errmsg)
                    os.remove(local_fullpath)
        # only download the file if it does not exist locally.
        if not os.path.isfile(local_fullpath):
            download_url = '{}:{}'.format(self.server, remote_url)
            # record the remote url for debugging and record-keeping
            with open(riders.remote_url, 'w') as f:
                f.write(remote_url)
            # record a representation of this query
            with open(riders.query_repr, 'w') as f:
                f.write(repr(self))
            cmd = [
                'gsiscp',
                download_url,
                local_fullpath
            ]
            complain("Running command in subprocess:", cmd)
            proc = Popen(cmd, **_PIPE_ARGS)
            res, err = proc.communicate()
            complain("RETVAL:", proc.returncode, "STDOUT:", res, "STDERR:",
                     err)
            if proc.returncode == 0:
                # get the remote sha256 sum
                try:
                    remote_sha256 = self.remote_sha256(remote_url)
                    with open(riders.remote_sha256, 'w') as f:
                        f.write(remote_sha256)
                except GWRemoteSha256Exception as e:
                    with open(riders.error_msg, 'a') as f:
                        f.write(e.args[0])
                    raise e
                # get the local sha256 sum
                try:
                    local_sha256 = self.local_sha256(remote_url)
                    with open(riders.local_sha256, 'w') as f:
                        f.write(local_sha256)
                except GWLocalSha256Exception as e:
                    with open(riders.error_msg, 'a') as f:
                        f.write(e.args[0])
                    raise e
            else:
                errtime = datetime.utcnow().isoformat()
                errfmt = "DOWNLOAD ERROR at {}. STDERR: \n{}\n"
                errmsg = errfmt.format(errtime, err)
                with open(riders.error_msg, 'a') as f:
                    f.write(errmsg)
                raise GWDataDownloadException(errmsg)

    def __repr__(self):
        fmt="{}('{}', '{}', '{}', framelength='{}', server='{}', outdir='{}')"
        return fmt.format(
            type(self).__name__,
            self.detector,
            self.frametype,
            self.gpstime,
            self.framelength,
            self.server,
            self.outdir
        )


def get_times(start, deltat, frlength):
    """Get a list of start times for frame files based on in initial starting
    time, ``start``, and a specified length of time, ``deltat``. The initial
    starting time will be rounded down to the nearest multiple of
    ``frlength``, the default length of a frame file, since these are
    the customary starting times for LVC frame files. Similarly, the time
    window ``deltat`` will be rounded up to the nearest multiple of
    ``frlength``."""
    start  = ( 
        int(np.floor(start // frlength)) * frlength
    )
    deltat = (
        int(np.ceil(deltat // frlength)) * frlength
    )
    return range(
        start,
        start + deltat + frlength,
        frlength
    )


def get_queries(
        start,
        deltat,
        length=DEFAULT_FRAME_LENGTH,
        server=DEFAULT_SERVER,
        outdir=DEFAULT_OUTDIR,
        h_frametypes=DEFAULT_H_FRAMETYPES,
        l_frametypes=DEFAULT_L_FRAMETYPES,
        v_frametypes=DEFAULT_V_FRAMETYPES
):
    """Get all GWFrameQuery objects in the given time window for each
    combination of detector and frametype provided.

    Args:

        ``start``       The GPS time at which the time window starts.
        ``deltat``      The width of the time window in seconds.
        ``length``      The expected duration in seconds of each frame file.
        ``server``      The server on which to look for frame files.
        ``outdir``      The output directory where downloaded files should be
                        saved.

    Additionally, one can specify the frame file types to be downloaded for
    each detector:

        ``h_frametypes``    Frametypes for LIGO Hanford.
        ``l_frametypes``    Frametypes for LIGO Livingston.
        ``v_frametypes``    Frametypes for Virgo.
    """
    queries = list()
    # add queries for each detector/frametype combination
    detector_frametypes_dict = {
        "H": h_frametypes,
        "L": l_frametypes,
        "V": v_frametypes
    }
    for detector in detector_frametypes_dict.keys():
        for frametype in detector_frametypes_dict[detector]:
            queries += [GWFrameQuery(detector, frametype, t,
                                     framelength=length, server=server,
                                     outdir=outdir)
                        for t in get_times(start, deltat, length)]
    return queries


def check_progress(queries):
    """Return a dictionary of lists of queries, where the keys of the
    dictionary indicate the status of each query."""
    status = {
        'all_queries':     queries,
        'downloaded':      [],
        'maybe_corrupt':   [],
        'corrupted':       [],
        'remote_found':    [],
        'remote_hashed':   [],
        'name_guessed':    [],
        'no_remote_found': [],
        'not_started':     [],
        'error':           []
    }
    for query in queries:
        riders = query.estimated_rider_fullpaths()
        if os.path.isfile(riders.local_sha256):
            if os.path.isfile(riders.remote_sha256):
                if filecmp.cmp(riders.local_sha256, riders.remote_sha256):
                    status['downloaded'].append(query)
                else:
                    status['corrupted'].append(query)
            else:
                status['maybe_corrupt'].append(query)
        elif os.path.isfile(riders.remote_sha256):
            status['remote_hashed'].append(query)
        elif os.path.isfile(riders.remote_url):
            status['remote_found'].append(query)
        elif os.path.isfile(riders.query_repr):
            status['name_guessed'].append(query)
        else:
            if os.path.isfile(riders.error_msg):
                status['error'].append(query)
                with open(riders.error_msg) as f:
                    lasterrmsg = f.readlines()[-1]
                if lasterrmsg == 'Cannot get GPS start time from filename: \n':
                    status['no_remote_found'].append(query)
            else:
                status['not_started'].append(query)
    return status


def display_progress(status):
    """Print how much progress has been made so far."""
    total_q = len(status['all_queries'])
    fmt = '{0: <16} {1} {2: >4}/{3: <4} ({4:>7.3f}%)'
    for key in status:
        number_of_queries = len(status[key])
        percent = (100.0 * number_of_queries) / total_q
        barticks = int(percent / 100. * PROG_BAR_WIDTH) 
        bar = "#"*barticks + "-"*(PROG_BAR_WIDTH-barticks)
        if number_of_queries == 0:
            bar = "-" + bar
        else:
            bar = "#" + bar
        print(fmt.format(key, bar, number_of_queries, total_q, percent))


def read_starts_and_deltats(infile):
    """Read ``start`` times and ``deltat`` values from an ``infile``, e.g.
    ``sys.stdin``.

    Returns
    -------

    ``times``   A list of ``(start, deltat)`` tuples, where ``start`` and
                ``deltat`` are times of the format expected by ``get_queries``.

    Arguments
    ---------

    ``infile``  A ``fileobj`` with one space-separated pair of (start, stop)
                times on each line (indicating the time intervals that should
                be downloaded).
    """
    start_stops = [l.split() for l in infile.readlines()]
    return [(int(start), int(stop)-int(start)) for start, stop in start_stops]


def main():
    complain("Arguments:", args)
    if args.times:
        times = read_starts_and_deltats(sys.stdin)
    elif args.start and args.deltat:
        times = [(args.start, args.deltat)]
    else:
        raise ValueError("Must provide either ``times`` or both of ``start`` "
                         "and ``deltat``.")
    queries = sum(
        [
            get_queries(
                start=start,
                deltat=deltat,
                length=args.length,
                server=args.server,
                outdir=args.outdir,
                h_frametypes=args.hanford_frametypes,
                l_frametypes=args.livingston_frametypes,
                v_frametypes=args.virgo_frametypes
            ) for start, deltat in times
        ],
        list()
    )
    complain("Queries:", *[format(q) for q in queries])
    complain("Total queries: ", len(queries))
    if args.progress:
        display_progress(check_progress(queries))
    else:
        if VERBOSE:
            complain("Checking progress before starting.")
            display_progress(check_progress(queries))
        tries_left = args.retries
        if tries_left >= 0:
            tries_left += 1
        while tries_left != 0:
            try:
                for query in queries:
                    if not query.estimated_fullpath_exists():
                        try:
                            query.download()
                        except FileNameParsingError:
                            complain('Filename parse error, skipping:', query)
            except Exception as err:
                complain("Exception caught:", err)
                if tries_left < 0:
                    complain("Retrying. Will keep trying till interrupted.")
                else:
                    tries_left -= 1
                    if tries_left != 0:
                        complain("Retrying. Tries left: {}".format(tries_left))
        if VERBOSE:
            complain("Done. Checking progress at end:")
            display_progress(check_progress(queries))

if __name__ == "__main__":
    main()
