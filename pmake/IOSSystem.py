import abc
import os
import stat
import tempfile
from typing import Union, List, Tuple, Dict, Any, Iterable

import semver

from pmake.InterestingPath import InterestingPath
from pmake.commons_types import path


class IOSSystem(abc.ABC):

    @abc.abstractmethod
    def _fetch_interesting_paths(self, script: "SessionScript") -> Dict[str, List[InterestingPath]]:
        """
        Fetch all the interesting paths relative to a operating system.
        Highly dependent on the operating system. Each path has associated different actual paths, since a single

        :param script: object used to interact with the system (if you need commands to fetch the paths)

        :return:
        """
        pass

    def create_temp_directory_with(self, directory_prefix: str) -> Any:
        """
        Create a temporary directory on the file system where to put temporary files

        :param directory_prefix: a prefix to be put before the temporary folder
        :return: a value which can be the input of a "with" statement. The folder will be automatically removed at the
        end of the with. The value returned is actually the absolute path of the temp directory
        """
        return tempfile.TemporaryDirectory(prefix=directory_prefix)

    def create_temp_file_with(self, directory: str, file_prefix: str = None, file_suffix: str = None, encoding: str = None, mode: str = None) -> Any:
        """
        Create a temporary file on the file system. The return value of this function is something you can give to the
        "with" statement. The file will be automatically remove at the end of the with. You can access the file absolute path
        via the field "name" of the return value

        :param directory: the directory where to put the file
        :param file_prefix: a string that will be put at the beginning of the filename
        :param file_suffix: a string that will be put at the end of the filename
        :param encoding: encoding used to open the file
        :param mode: the mode used to open the file. E.g., "w", "r", "w+". See open for further information
        :return: a return value that can be used as input of with statement
        """
        return tempfile.NamedTemporaryFile(
            mode=mode,
            encoding=encoding,
            prefix=file_prefix,
            suffix=file_suffix,
            dir=directory,
            delete=True
        )

    def create_temp_file(self, directory: str, file_prefix: str = None, file_suffix: str = None, readable_for_all: bool = False, executable_for_owner: bool = False, executable_for_all: bool = False) -> path:
        """
        Creates the file
        Like ::create_temp_file_with, but the file needs to be manually removed

        :param directory: the directory where to put the file
        :param file_prefix: a string that will be put at the beginning of the filename
        :param file_suffix: a string that will be put at the end of the filename
        :param readable_for_all: if True, the file can be read by anyone
        :param executable_for_owner: if True, the file can be executed by the owner
        :param executable_for_all: if True, anyone can execute the file
        :return: the absolute path of the temp file
        """
        fd, file_path = tempfile.mkstemp(
            prefix=file_prefix,
            suffix=file_suffix,
            dir=directory,
        )
        os.close(fd)
        if readable_for_all:
            self.mark_file_as_readable_by_all(file_path)
        if executable_for_all:
            self.mark_file_as_executable_by_all(file_path)
        if executable_for_owner:
            self.mark_file_as_executable_by_owner(file_path)

        return file_path

    def mark_file_as_readable_by_user(self, file_path: path):
        """
        Mark the file as readable by the owner

        :param file_path: the file involved
        """
        st = os.stat(file_path)
        os.chmod(file_path, mode=st.st_mode | stat.S_IRUSR)

    def mark_file_as_executable_by_owner(self, file_path: path):
        """
        Mark the filev as executable by the owner

        :param file_path: the file involved
        """
        st = os.stat(file_path)
        os.chmod(file_path, mode=st.st_mode | stat.S_IXUSR)

    def mark_file_as_readable_by_all(self, file_path: path):
        """
        Mark the file as readable by all

        :param file_path: the file involved
        """
        st = os.stat(file_path)
        os.chmod(file_path, mode=st.st_mode | stat.S_IROTH)

    def mark_file_as_executable_by_all(self, file_path: path):
        """
        Mark the filev as executable by all

        :param file_path: the file involved
        """
        st = os.stat(file_path)
        os.chmod(file_path, mode=st.st_mode | stat.S_IXOTH)

    @abc.abstractmethod
    def get_current_username(self) -> str:
        pass

    @abc.abstractmethod
    def execute_command(self, commands: List[Union[str, List[str]]], show_output_on_screen: bool, capture_stdout: bool, cwd: str = None, env: Dict[str, Any] = None, check_exit_code: bool = True, timeout: int = None, execute_as_admin: bool = False, admin_password: str = None, log_entry: bool = False) -> Tuple[int, str, str]:
        """
        Execute an arbitrary command

        :param commands: the commands to execute. They need to be executed in the same environment. Can either be a list of strnigs or a string
        :param show_output_on_screen: if True, we need to display in real time the stdout of the command on the stdout of pmake as well
        :param capture_stdout: if True, we need to return the stdout of the command
        :param cwd: directory where the command will be executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :param execute_as_admin: if True, we will elevate our current user to admin privileges. We assume this operation requires no input for the user.
        :param admin_password: **[UNSAFE!!!!]** If you **really** need, you might want to run a command as an admin
            only on your laptop, and you want a really quick and dirty way to execute it, like as in the shell.
            Do **not** use this in production code, since the password will be 'printed in clear basically everywhere!
            (e.g., history, system monitor, probabily in a file as well)
        :param log_entry: if True, we will emit on the console what we are executing
        :return: triple. The first element is the error code, the second is the stdout (if captured), the third is stderr
        """
        pass

    @abc.abstractmethod
    def is_program_installed(self, program_name: str) -> bool:
        """
        Check if a program is installed on the platform.

        :param program_name: name of the program
        :return: true if the program is installed on the system, false otherwise4
        """
        pass

    @abc.abstractmethod
    def get_env_variable(self, name: str) -> str:
        """
        Get an evnironment variable value.
        We will use the current user environment to determine the variable
        Raises an exception if the variable does not exist

        :param name: the environment variable to fetch
        :return: the environmkent variable value
        """
        pass

    @abc.abstractmethod
    def get_home_folder(self) -> path:
        """
        Get the absolute home folder of the current user
        """
        pass

    def ls(self, folder: path, generate_absolute_path: bool = False) -> Iterable[path]:
        """
        Show the list of all the files in the given directory

        :param folder: folder to scan.
        :param generate_absolute_path: if true, we will generate in the outptu the absolute path of the subfolders.
            Otherwise we will return only the
        :return: iterable of files in the directory
        """
        for x in os.listdir(folder):
            if generate_absolute_path:
                yield os.path.abspath(os.path.join(folder, x))
            else:
                yield x

    def ls_only_files(self, folder: path, generate_absolute_path: bool = False) -> Iterable[path]:
        """
        Show the list of all the files (but not directories) in the given directory

        :param folder: folder to scan.
        :param generate_absolute_path: if true, we will generate in the outptu the absolute path of the subfolders. Otherwise we will return only the
        :return: iterable of files in the directory
        """
        for f in os.listdir(folder):
            if os.path.isfile(f):
                if generate_absolute_path:
                    yield os.path.abspath(os.path.join(folder, f))
                else:
                    yield f

    def ls_only_directories(self, folder: path, generate_absolute_path: bool = False) -> Iterable[path]:
        """
        Show the list of all the directories in the given directory

        :param folder: folder to scan.
        :param generate_absolute_path: if true, we will generate in the outptu the absolute path of the subfolders.
            Otherwise we will return only the names
        :return: iterable of folders in directory
        """
        for f in os.listdir(folder):
            if os.path.isdir(os.path.abspath(os.path.join(folder, f))):
                if generate_absolute_path:
                    yield os.path.abspath(os.path.join(folder, f))
                else:
                    yield f

    def _get_semantic_version(self, s: str) -> semver.VersionInfo:
        if len(s.split(".")) == 1:
            return semver.VersionInfo.parse(f"{s}.0.0")
        elif len(s.split(".")) == 2:
            return semver.VersionInfo.parse(f"{s}.0")
        else:
            return semver.VersionInfo.parse(s)

    def _fetch_latest_paths(self, script: "SessionScript", interesting_paths: Dict[str, List[InterestingPath]]) -> Dict[str, InterestingPath]:
        """
        geenrate the latest interesting paths
        :param script:
        :return:
        """

        result = {}

        architecture = script.get_architecture()

        for k, values in interesting_paths.items():
            # remove all the paths which are not involved in the current architecture
            tmp = list(filter(lambda x: x.architecture == architecture, values))
            # fetch the path with the latest version
            max_interesting_path = None
            for x in tmp:
                if max_interesting_path is None:
                    max_interesting_path = x
                elif x.version > max_interesting_path.version:
                    max_interesting_path = x

            result[k] = max_interesting_path

        return result

    def _convert_stdout(self, stdout) -> str:
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8")
        elif isinstance(stdout, list):
            tmp = []
            for x in stdout:
                if isinstance(x, bytes):
                    tmp.append(x.decode("utf-8"))
                elif isinstance(x, str):
                    tmp.append(x)
                else:
                    raise TypeError(f"invalid stdout output type {type(x)}!")
            stdout = ''.join(tmp)
        elif isinstance(stdout, str):
            stdout = str(stdout)
        else:
            raise TypeError(f"invalid stdout output type {type(stdout)}!")

        return stdout.strip()