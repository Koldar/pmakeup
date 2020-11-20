import abc
import inspect
import logging
import os
import re
import shutil
import stat
import sys

import colorama

import urllib.request
from typing import List, Union, Iterable, Tuple, Any, Callable, Dict

import semver

from pmake import LinuxOSSystem, version
from pmake import PMakeModel
from pmake import WindowsOSSystem
from pmake.commons_types import path
from pmake.exceptions.PMakeException import AssertionPMakeException, PMakeException


class SessionScript(abc.ABC):
    """
    Contains all the commands available for the user in a PMakefile.py file
    """

    def __init__(self, model: "PMakeModel"):
        self._model = model
        self._cwd = os.path.abspath(os.curdir)
        self._locals = {}
        self._foreground_mapping = {
            "RED": colorama.Fore.RED,
            "GREEN": colorama.Fore.GREEN,
            "YELLOW": colorama.Fore.YELLOW,
            "BLUE": colorama.Fore.BLUE,
            "MAGENTA": colorama.Fore.MAGENTA,
            "CYAN": colorama.Fore.CYAN,
            "WHITE": colorama.Fore.WHITE,
        }
        self._background_mapping = {
            "RED": colorama.Back.RED,
            "GREEN": colorama.Back.GREEN,
            "YELLOW": colorama.Back.YELLOW,
            "BLUE": colorama.Back.BLUE,
            "MAGENTA": colorama.Back.MAGENTA,
            "CYAN": colorama.Back.CYAN,
            "WHITE": colorama.Back.WHITE,
        }
        self._disable_log_command: bool = False
        if self.on_windows():
            self._platform = WindowsOSSystem.WindowsOSSystem()
        elif self.on_linux():
            self._platform = LinuxOSSystem.LinuxOSSystem()
        else:
            raise PMakeException(f"Cannot identify platform!")

        # fetches the interesting paths
        self._interesting_paths = self._platform._fetch_interesting_paths(self)
        self._latest_interesting_path = self._platform._fetch_latest_paths(self, self._interesting_paths)

    def get_latest_path_with_architecture(self, current_path: str, architecture: int) -> path:
        """
        get the latest path on the system with the specified archietcture

        :param current_path: nominal path name
        :param architecture: either 32 or 64
        :return: the first path compliant with this path name
        """
        max_x = None
        for x in filter(lambda x: x.architecture == architecture, self._interesting_paths[current_path]):
            if max_x is None:
                max_x = x
            elif x.version > max_x.version:
                max_x = x

        return max_x.path

    @staticmethod
    def _list_all_commands() -> Iterable[Tuple[str, str]]:

        def get_str(t: Any) -> str:
            if hasattr(t, "__name__"):
                return t.__name__
            else:
                return str(t)

        for command_name in filter(lambda x: not x.startswith("_"), dir(SessionScript)):
            method = getattr(SessionScript, command_name)
            fullargspec = inspect.getfullargspec(method)
            arg_tmp = []
            if 'return' in fullargspec.annotations:
                result_type = get_str(fullargspec.annotations["return"])
            else:
                result_type = "None"
            for x in fullargspec.args[1:]:
                if x in fullargspec.annotations:
                    param_type = get_str(fullargspec.annotations[x])
                else:
                    param_type = "Any"
                arg_tmp.append(f"{x}: {param_type}")
            method_signature = f"{command_name} ({', '.join(arg_tmp)}) -> {result_type}"

            yield method_signature, method.__doc__

    def _color_str(self, message: str, foreground: str = None, background: str = None) -> str:
        """
        Color a string

        :param message: string involved
        :param foreground: foreground color of the string. Accepted values: RED, GREEN, YELLOW, BLUE, MAGENT, CYAN, WHITE
        :param background: background color of the string. Accepted values: RED, GREEN, YELLOW, BLUE, MAGENT, CYAN, WHITE
        :return: colored string
        """
        result = ""
        should_reset = False
        if foreground is not None:
            result += str(self._foreground_mapping[foreground.upper()])
            should_reset = True
        if background is not None:
            result += str(self._background_mapping[background.upper()])
            should_reset = True
        result += str(message)
        if should_reset:
            result += colorama.Style.RESET_ALL

        return result

    def ensure_condition(self, condition: Callable[[], bool], message: str = "") -> None:
        """
        Perform a check. If the condition is **not** satisfied, we raise exception

        :param condition: the condition to check. generate exception if the result is False
        :param message: the message to show if the exception needs to be generated
        """

        if not condition():
            raise AssertionPMakeException(f"pmake needs to generate a custom exception: {message}")

    def ensure_has_variable(self, name: str) -> None:
        """
        Ensure the user has passed a variable via "--variable" CLI utils.
        If not, an exception is generated

        :param name: the variable name to check

        """
        return self.ensure_condition(lambda: name in self._model.variable, message=f"""No variable passed with "--variable" named "{name}".""")

    def semantic_version_2_only_core(self, filename: str) -> semver.VersionInfo:
        """
        A function that can be used within ::get_latest_version_in_folder

        :param filename: the absolute path of a file that contains a version
        :return: the version
        """
        regex = r"\d+\.\d+\.\d+"
        b = os.path.basename(filename)
        m = re.search(regex, b)
        logging.debug(f"checking if \"{filename}\" satisfies \"{regex}\"")
        if m is None:
            raise PMakeException(f"Cannot find the regex {regex} within file \"{b}\"!")
        logging.debug(f"yes: \"{m.group(0)}\"")
        return semver.VersionInfo.parse(m.group(0))

    def quasi_semantic_version_2_only_core(self, filename: str) -> semver.VersionInfo:
        """
        A function that can be used within ::get_latest_version_in_folder.
        It accepts values like "1.0.0", but also "1.0" and "1"

        :param filename: the absolute path of a file that contains a version
        :return: the version
        """
        regex = r"\d+(?:\.\d+(?:\.\d+)?)?"
        b = os.path.basename(filename)
        m = re.search(regex, b)
        if m is None:
            raise PMakeException(f"Cannot find the regex {regex} within file \"{b}\"!")
        result = m.group(0)
        if len(result.split(".")) == 2:
            result += ".0"
        if len(result.split(".")) == 1:
            result += ".0.0"
        return semver.VersionInfo.parse(result)

    def get_latest_version_in_folder(self, folder: path = None, should_consider: Callable[[path], bool] = None, version_fetcher: Callable[[str], semver.VersionInfo] = None) -> Tuple[semver.VersionInfo, List[path]]:
        """
        Scan the subfiles and subfolder of a given directory. We assume each file or folder has a version withint it.
        Then fetches the latest version.
        This command is useful in dierctories where all releases of a given software are placed. if we need to fetch
        the latest one,
        this function is perfect for the task.

        :param folder: the folder to consider. If unspecified, it is the current working directory
        :param should_consider: a function that allows you to determine if we need to consider or
            not a subfile/subfolder. The input isan absolute path. If no function is given, we accept all the
            sub files
        :param version_fetcher: a function that extract a version from the filename. If left unspecified, we will
            use ::semantic_version_2_only_core
        :return: the latest version in the folder. The second element of the tuple is a collection of all the filenames
            that specify the latest version
        """

        if folder is None:
            folder = self._cwd
        if should_consider is None:
            should_consider = lambda x: True
        if version_fetcher is None:
            version_fetcher = self.quasi_semantic_version_2_only_core
        p = self.get_path(folder)

        result_version = None
        result_list = []
        for file in self._platform.ls(p, generate_absolute_path=True):
            logging.debug(f"Shuld we consider {file} for fetching the latest version?")
            if not should_consider(file):
                continue
            # find the version
            v = version_fetcher(file)
            logging.debug(f"fetched version {v}. Latest version detected up until now is {result_version}")
            if result_version is None:
                result_version = v
                result_list = [file]
                logging.debug(f"update version with {result_version}. Files are {' '.join(result_list)}")
            elif v > result_version:
                result_version = v
                result_list = [file]
                logging.debug(f"update version with {result_version}. Files are {' '.join(result_list)}")
            elif v == result_version:
                result_list.append(file)
                logging.debug(f"update version with {result_version}. Files are {' '.join(result_list)}")

        return result_version, result_list

    def _truncate_string(self, string: str, width: int, ndots: int = 3) -> str:
        if len(string) > (width - ndots):
            return string[:(width-ndots)] + "."*ndots
        else:
            return string

    def get_architecture(self) -> int:
        """
        check if the system is designed on a 32 or 64 bits

        :return: either 32 or 64 bit
        """
        is_64 = sys.maxsize > 2**32
        if is_64:
            return 64
        else:
            return 32

    def on_windows(self) -> bool:
        """
        Check if we are running on windows

        :return: true if we are running on windows
        """
        self._log_command(f"Checking if we are on a windows system")
        return os.name == "nt"

    def on_linux(self) -> bool:
        """
        Check if we are running on linux

        :return: true if we are running on linux
        """
        self._log_command(f"Checking if we are on a linux system")
        return os.name == "posix"

    def clear_cache(self):
        """
        Clear the cache of pmake
        """
        self._model.pmake_cache.reset()

    def set_variable_in_cache(self, name: str, value: Any, overwrite_if_exists: bool = True):
        """
        Set a variable inside the program cache. Setting variable in cache allows pmake to
        store information between several runs of pmake.

        How pmake stores the information is implementation dependent and it should not be relied upon

        :param name: name of the variable to store
        :param value: object to store
        :param overwrite_if_exists: if true, if the cache already contain a variable with the same name, such a varaible will be replaced
            with the new one
        """
        self._log_command(f"Setting {name}={value} in cache")
        self._model.pmake_cache.set_variable_in_cache(
            name=name,
            value=value,
            overwrites_is_exists=overwrite_if_exists
        )

    def has_variable_in_cache(self, name: str) -> bool:
        """
        Check if a variable is in the pmake cache

        :param name: name of the variable to check
        :return: true if a varaible with such a name is present in the cache, false otherwise
        """
        return self._model.pmake_cache.has_variable_in_cache(
            name=name
        )

    def get_variable_in_cache(self, name: str) -> Any:
        """
        Get the variable from the cache. if the variable does not exist, an error is generated

        :param name: name of the variable to check
        :return: the value associated to such a variable
        """
        return self._model.pmake_cache.get_variable_in_cache(
            name=name
        )

    def get_variable_in_cache_or(self, name: str, default: Any) -> Any:
        """
        Get the variable value from the cache or get a default value if it does not exist

        :param name: name of the variable to fetch
        :param default: if the variable does not exist in the cache, the value to retturn from this function
        :return: the variable value
        """
        if self._model.pmake_cache.has_variable_in_cache(name):
            return self._model.pmake_cache.get_variable_in_cache(name)
        else:
            return default

    def add_or_update_variable(self, name: str, supplier: Callable[[], Any], mapper: Callable[[Any], Any]):
        """
        Add a new variable in the cache

        :param name: the variable to set
        :param supplier: function used to generate the value fo the variable if the variable does not exist in the cache
        :param mapper: function used to generate the value fo the variable if the variable does exist in the cache. The input
            is the variable old value
        """
        if self._model.pmake_cache.has_variable_in_cache(name):
            new_value = mapper(self._model.pmake_cache.get_variable_in_cache(name))
        else:
            new_value = supplier()
        self._log_command(f"Setting {name}={new_value} in cache")
        self._model.pmake_cache.set_variable_in_cache(name, new_value)

    def get_starting_cwd(self) -> path:
        """
        :return: absolute path of where you have called pmake
        """
        return self._model.starting_cwd

    def get_pmakefile_path(self) -> path:
        """
        :return: absolute path of the main PMakefile path
        """
        return self._model.input_file

    def get_home_folder(self) -> path:
        """
        Get the home fodler of the currently logged used
        """
        return self._platform.get_home_folder()

    def get_pmakefile_dirpath(self) -> path:
        """
        :return: absolute path of the folder containing the main PMakefile path
        """
        return os.path.dirname(self._model.input_file)

    def echo(self, message: str, foreground: str = None, background: str = None):
        """
        Print a message on the screen

        :param message: the message to print out
        :param foreground: foreground color of the string. Accepted values: RED, GREEN, YELLOW, BLUE, MAGENT, CYAN, WHITE
        :param background: background color of the string. Accepted values: RED, GREEN, YELLOW, BLUE, MAGENT, CYAN, WHITE
        """

        self._log_command(f"""echo \"{message}\"""")
        print(self._color_str(message, foreground, background))

    def specifies_target(self, target_name: str) -> bool:
        """
        Check if the the user has specified the given target

        :param target_name: the name of the target that we need to check
        :return: true if the target has been declard by the user, false otherwise
        """
        return target_name in self._model.targets

    def require_pmake_version(self, lowerbound: str) -> None:
        """
        Check if the current version of pmake is greater or equal than the given one.
        If the current version of pmake is not compliant with this constraint, an error is generated

        :param lowerbound: the minimum version this script is compliant with
        """
        system_version = semver.VersionInfo.parse(version.VERSION)
        script_version = semver.VersionInfo.parse(lowerbound)
        self._log_command(f"Checking if script minimum pmake version ({script_version}) is compliant with pmake version ({system_version})")
        if lowerbound < version.VERSION:
            raise PMakeException(f"The script requires at least version {script_version} to be installed. Current version is {system_version}")

    def pairs(self, it: Iterable[Any]) -> Iterable[Tuple[Any, Any]]:
        """
        Convert the iterable into an iterable of pairs.

        1,2,3,4,5,6 becomes (1,2), (2,3), (3,4), (4,5), (5,6)

        :param it: iterable whose sequence we need to generate
        :return: iterable of pairs
        """
        previous = None
        for x in it:
            if previous is None:
                previous = x
            else:
                yield previous, x
                previous = x

    def grep(self, lines: Iterable[str], regex: str, reverse_match: bool = False) -> Iterable[str]:
        """
        Filter the lines fetched from terminal

        :param lines: the lines to fetch
        :param regex: a python regex. If a line contains a substring which matches the given regex, the line is returned
        :param reverse_match: if True, we will return lines which do not match the pattern
        :return: lines compliant with the regex
        """
        for line in lines:
            m = re.search(regex, line)
            if reverse_match:
                if m is None:
                    yield line
            else:
                if m is not None:
                    yield line

    def get_column_of_table(self, table: List[List[str]], index: int) -> List[str]:
        """
        Select a single column from the table, generated by ::convert_table

        :param table: the table generated by ::convert_table
        :param index: index of the column to return. Starts from 0
        :return: the column requested
        """
        return list(map(lambda x: x[index], table))

    def get_column_of_table_by_name(self, table: List[List[str]], column_name: str) -> List[str]:
        """
        Select a single column from the table, generated by ::convert_table
        We assumed the first row of the table is a header, contaiing the column names

        :param table: the table generated by ::convert_table
        :param column_name: name of the column to return.
        :return: the column requested
        """
        header = table[0]
        column_index = None
        for index, name in enumerate(header):
            if name == column_name:
                column_index = index
                break
        if column_index is None:
            raise PMakeException(f"Cannot find column named '{column_name}' in header: {', '.join(header)}")

        return self.get_column_of_table(table, column_index)

    def convert_table(self, table_str: str) -> List[List[str]]:
        """
        Convert a table printed as:

        Port         Type              Board Name              FQBN                 Core
        /dev/ttyACM1 Serial Port (USB) Arduino/Genuino MKR1000 arduino:samd:mkr1000 arduino:samd

        Into a list of lists of strings

        :param table_str: representation of a table
        :return: list of lists of strings
        """

        def is_column(index: int, lines: List[str]) -> bool:
            column_found = True
            # a column is found when in all lines the same character is " "
            for line in lines:
                # the char in index needs to be a whitespace for all lines
                column_found = line[index] == " "
                if not column_found:
                    return False
                # the char after index needs not to be a whitespace for all lines
                if 0 < (index + 1) < len(line):
                    column_found = line[index + 1] != " "
                if not column_found:
                    return False
            return True

        column_index = [0]
        result = []
        lines = list(
            filter(
                lambda x: len(x) > 0,
                map(
                    lambda x: x.strip(),
                    table_str.split("\n")
                )
            )
        )
        min_length = min(map(lambda x: len(x), lines))
        for index in range(min_length):
            if is_column(index, lines):
                column_index.append(index + 1)
        # append last column
        column_index.append(-1)

        for line in lines:
            tmp = []
            for (start, end) in self.pairs(column_index):
                if end == -1:
                    tmp.append(line[start:].strip())
                else:
                    tmp.append(line[start:(end-1)].strip())
            result.append(tmp)

        return result

    def _log_command(self, message: str):
        """
        reserved. Useful to log the action performed by the user

        :param message: message to log
        """
        if not self._disable_log_command:
            logging.info(message)

    def info(self, message: str):
        """
        Log a message using 'INFO' level

        :param message: the message to log
        """
        logging.info(message)

    def critical(self, message: str):
        """
        Log a message using 'CRITICAL' level

        :param message: the message to log
        """
        logging.critical(message)

    def debug(self, message: str):
        """
        Log a message using 'DEBUG' level

        :param message: the message to log
        """
        logging.debug(message)

    def create_empty_file(self, name: path, encoding: str = "utf-8"):
        """
        Create an empty file. if the file is relative, it is relative to the CWD

        :param name: file name to create
        :param encoding: encoding of the file. If unspecified, it is utf-8
        """
        p = self.get_path(name)
        self._log_command(f"Creating empty file {p}")
        with open(p, "w", encoding=encoding) as f:
            pass

    def is_program_installed(self, program_name: str) -> bool:
        """
        Check if a program is reachable via commandline

        :param program_name: the name of the program (e.g., dot)
        :return: true if there is a program accessible to the PATH with the given name, false otherwise
        """
        return self._platform.is_program_installed(program_name)

    def create_empty_directory(self, name: path):
        """
        Create an empty directory in the CWD (if the path is relative)

        :param name:the name of the driectory to create
        """
        p = self.get_path(name)
        os.makedirs(name=p, exist_ok=True)

    def is_file_exists(self, name: path) -> bool:
        """
        Check if a file exists

        :param name: file whose existence we need to assert
        :return: true if the file exists, false otherwise
        """
        p = self.get_path(name)
        self._log_command(f"Checking if the file {p} exists")
        return os.path.exists(p)

    def is_file_empty(self, name: path) -> bool:
        """
        Checks if a file exists. If exists, check if it empty as well.

        :param name: file to check
        :return: true if the file exists **and** has no bytes; false otherwise
        """
        p = self.get_path(name)
        self._log_command(f"Checking if the file {p} exists and is empty")
        if not os.path.exists(p):
            return False
        with open(p, "r") as f:
            return f.read(1) == ""

    def is_directory_exists(self, name: path) -> bool:
        """
        Check if a directory exists.

        :param name: folder to check
        :return: true if the folder exists, false otherwise
        """
        p = self.get_path(name)
        self._log_command(f"Checking if the folder {p} exists")
        if os.path.exists(p) and os.path.isdir(p):
            return True
        return False

    def is_directory_empty(self, name: path) -> bool:
        """
        Check if a directory exists and is empty

        :param name: folder to check
        :return: true if the folder exists and is empty, false otherwise
        """
        p = self.get_path(name)
        self._log_command(f"Checking if the folder {p} exists and is empty")
        if os.path.exists(p) and os.path.isdir(p):
            return len(os.listdir(p)) == 0
        return False

    def is_file_non_empty(self, name: path) -> bool:
        """
        Checks if a file exists. If exists, check if it is not empty as well.

        :param name: file to check
        :return: true if the file exists **and** has at least one byte; false otherwise
        """
        p = self.get_path(name)
        self._log_command(f"Checking if the file {p} exists and is empty")
        if not os.path.exists(p):
            return False
        with open(p, "r") as f:
            return f.read(1) != ""

    def write_file(self, name: path, content: Any, encoding: str = "utf-8", overwrite: bool = False, add_newline: bool = True):
        """
        Write into a file with the specified content. if overwrite is unset, we will do nothing if the file already exists

        :param name: name of the file to create
        :param content: content of the file to create.
        :param encoding: encoding fo the file to create. utf-8 by default
        :param overwrite: if true, we will overwrite the file
        :param add_newline: if true, we will add a new line at the end of the content
        """

        p = self.get_path(name)
        self._log_command(f"Writing file {p} with content {self._truncate_string(content, 20)}")
        if not overwrite and os.path.exists(p):
            return
        else:
            with open(p, "w", encoding=encoding) as f:
                f.write(str(content))
                if add_newline:
                    f.write("\n")

    def write_lines(self, name: path, content: Iterable[Any], encoding: str = "utf-8", overwrite: bool = False):
        """
        Write severla lines into a file. if overwrite is unset, we will do nothing if the file already exists

        :param name: name of the file to create
        :param content: lines of the file to create. We will append a new ine at the end of each line
        :param encoding: encoding fo the file to create. utf-8 by default
        :param overwrite: if true, we will overwrite the file
        """

        p = self.get_path(name)
        self._log_command(f"Writing file {p} with content {len(list(content))} lines")
        if not overwrite and os.path.exists(p):
            return
        else:
            with open(p, "w", encoding=encoding) as f:
                for x in content:
                    f.write(str(x) + "\n")

    def read_lines(self, name: path, encoding: str = "utf-8") -> Iterable[str]:
        """
        Read the content of a file and yields as many item as there are lines in the file.
        Strip from the line ending new lines. Does not consider empty lines

        :param name: name of the file
        :param encoding: encoding of the file. If unspecified, it is utf-8
        :return: iterable containing the lines of the file
        """
        p = self.get_path(name)
        self._log_command(f"Reading lines from file {p}")
        with open(p, "r", encoding=encoding) as f:
            for line in f.readlines():
                if line is None:
                    continue
                if line.strip() == "":
                    continue
                yield line.rstrip("\n\r")

    def read_file_content(self, name: path, encoding: str = "utf-8", trim_newlines: bool = True) -> str:
        """
        Read the whole content of the file in a single string

        :param name: name of the file to load
        :param encoding: the encoding of the file. If unspecified, it is utf-8
        :param trim_newlines: if true, we will trim the newlines, spaces and tabs at the beginning and at the end of the file
        :return: string repersenting the content of the file
        """
        p = self.get_path(name)
        self._log_command(f"Reading file {p} content")
        with open(p, "r", encoding=encoding) as f:
            result = f.read()
        if trim_newlines:
            result = result.strip("\t\n\r ")
        return result

    def remove_last_n_line_from_file(self, name: path, n: int = 1, consider_empty_line: bool = False, encoding: str = "utf-8") -> List[str]:
        """
        Read the content of a file and remove the last n lines from the file involved. Then, rewrites the whole file

        :param name: file involved. If relative, it is relative to ::cwd()
        :param n: the number of lines to remove at the end.
        :param consider_empty_line: if True, we consider empty lines as well.
        :param encoding: the encoding used to rewrite file
        :return: the lines just removed
        """

        p = self.get_path(name)

        self._log_command(f"Remove {n} lines at the end of file {p} (consider empty line = {consider_empty_line})")
        with open(name, mode="r", encoding=encoding) as f:
            lines = list(f.readlines())

        result = []
        final_i = 0
        for i, line in enumerate(reversed(lines)):
            if final_i == n:
                break

            if consider_empty_line and line.strip() == "":
                result.append(line)
                continue
            result.append(line)
            final_i += 1

        # write the file
        with open(name, mode="w", encoding=encoding) as f:
            f.writelines(lines[:-final_i])

        return result

    def append_string_at_end_of_file(self, name: path, content: Any, encoding: str = "utf-8") -> None:
        """
        Append a string at the end of the file. carriage return is automatically added

        :param name: filename
        :param content: string to append
        :param encoding: encoding of the file. If missing, "utf-8" is used
        """
        self.append_strings_at_end_of_file(
            name=name,
            content=[content],
            encoding=encoding
        )

    def append_strings_at_end_of_file(self, name: path, content: Iterable[Any], encoding: str = "utf-8") -> None:
        """
        Append a string at the end of the file. carriage return is automatically added

        :param name: filename
        :param content: string to append
        :param encoding: encoding of the file. If missing, "utf-8" is used
        """
        p = self.get_path(name)
        self._log_command(f"Appending {content} into file file {p}")
        with open(p, "a", encoding=encoding) as f:
            for x in content:
                f.write(str(x) + "\n")

    def copy_file(self, src: path, dst: path):
        """
        Copy a single file from a position to another one

        :param src: file to copy
        :param dst: destination where the file will be copied to
        """
        asrc = self.get_path(src)
        adst = self.get_path(dst)
        self._log_command(f"""copy file from \"{asrc}\" to \"{adst}\"""")
        shutil.copyfile(asrc, adst)

    def copy_tree(self, src: path, dst: path):
        """
        Copy a whole directory tree or a single file

        :param src: the folder or the file to copy.
        :param dst: the destination where the copied folder will be positioned
        """
        asrc = self.get_path(src)
        adst = self.get_path(dst)
        self._log_command(f"""Recursively copy files from \"{asrc}\" to \"{adst}\"""")
        if os.path.isdir(asrc):
            shutil.copytree(
                asrc,
                adst,
            )
        elif os.path.isfile(asrc):
            shutil.copyfile(
                asrc,
                adst
            )
        else:
            raise InvalidScenarioPMakeException(f"Cannot determine if {asrc} is a file or a directory!")

    def copy_folder_content(self, folder: path, destination: path):
        """
        Copy all the content of "folder" into the folder "destination"

        :param folder: folder to copy files from
        :param destination: folder where the contents will be copied into
        """
        afolder = self.get_path(folder)
        adestination = self.get_path(destination)
        self._log_command(f"""Copies all files inside \"{afolder}\" into the folder \"{adestination}\"""")

        try:
            self._disable_log_command = False
            for x in self.ls(afolder, generate_absolute_path=False):
                self.copy_tree(
                    src=os.path.join(afolder, x),
                    dst=os.path.abspath(os.path.join(adestination, x))
                )
        finally:
            self._disable_log_command = True

    def download_url(self, url: str, destination: path = None, ignore_if_file_exists: bool = True) -> path:
        """
        Download an artifact from internet

        :param url: the url where the file is lcoated
        :param destination: the folder where the file will be created
        :param ignore_if_file_exists: if true, we will not perform the download at all
        :return: path containing the downloaded item
        """
        dst = self.get_path(destination)
        self._log_command(f"""Downloading {url} from internet into {dst}""")
        if ignore_if_file_exists and os.path.exists(dst):
            return dst

        result, http_message = urllib.request.urlretrieve(url, dst)
        return result

    def allow_file_to_be_executed_by_anyone(self, file: path) -> None:
        """
        Allow the file to be executed by anyone. On a linux system it should be equal to "chmod o+x"

        :param file: the file whose permission needs to be changed
        """
        p = self.get_path(file)
        self._log_command(f"""Allowing any user to unr {p}""")
        os.chmod(p, mode=stat.S_IEXEC)

    def copy_files_that_basename(self, src: path, dst: path, regex: str):
        """
        Copy the files located (directly or indirctly) in src into dst.
        We will copy only the files whose basename (e.g. foo.txt is the basename of /opt/foo/bar/foo.txt).
        We will copy the directories where a file is located as well
        matches the given regex

        :param src: folder where we will find files to copy
        :param dst: destination of the files
        :param regex: regex that determines wether or not a file is copies
        :return:
        """
        s = self.get_path(src)
        d = self.get_path(dst)
        self._log_command(f"Copy files from {s} into {d} which basename follows {regex}")
        try:
            self._disable_log_command = False
            for x in self.ls_recursive(src):
                if re.search(pattern=regex, string=os.path.basename(x)):
                    rel = os.path.relpath(x, s)
                    copied_d = os.path.abspath(os.path.join(d, rel))
                    os.makedirs(os.path.dirname(copied_d), exist_ok=True)
                    shutil.copyfile(src=x, dst=copied_d)
        finally:
            self._disable_log_command = True

    def move_tree(self, src: path, dst: path):
        self._log_command(f"""Recursively move files from \"{src}\" to \"{dst}\"""")
        self.copy_tree(src, dst)
        self.remove_tree(src)

    def remove_tree(self, src: path, ignore_if_not_exists: bool = True):
        self._log_command(f"""Recursively remove files from \"{src}\"""")
        try:
            shutil.rmtree(src)
        except Exception as e:
            if not ignore_if_not_exists:
                raise e

    def remove_files_that_basename(self, src: path, regex: str):
        """
        Remove the files located (directly or indirectly) in src.
        We will copy only the files whose basename (e.g. foo.txt is the basename of /opt/foo/bar/foo.txt).
        We will copy the directories where a file is located as well
        matches the given regex

        :param src: folder where we will find files to copy
        :param regex: regex that determines wether or not a file is copies
        :return:
        """
        s = self.get_path(src)
        self._log_command(f"Remove the files from {s} which basename follows {regex}")
        try:
            self._disable_log_command = False
            for x in self.ls_recursive(src):
                logging.debug(f"Checking if {x} should be removed")
                if re.search(pattern=regex, string=os.path.basename(x)):
                    try:
                        logging.debug(f"Removing {x}")
                        os.unlink(x)
                    except Exception as e:
                        pass
        finally:
            self._disable_log_command = True

    def move_file(self, src: path, dst: path):
        """
        Move a single file from a location to another one

        :param src: the file to move
        :param dst: the path where the file will be moved to
        """
        asrc = self.get_path(src)
        adst = self.get_path(dst)
        self._log_command(f"""move file from \"{asrc}\" to \"{adst}\"""")
        shutil.move(asrc, adst)

    def remove_file(self, name: path, ignore_if_not_exists: bool = True) -> bool:
        """
        Remove a file. If the cannot be removed (for some reason), ignore_if_not_exists determines if somethign goes wrong

        :param name: file to delete
        :param ignore_if_not_exists: if true, we won't raise exception if the file does not exists or cannot be removed
        :return: true if we have removed the file, false otherwise
        """
        p = self.get_path(name)
        self._log_command(f"remove file {p}")
        try:
            os.unlink(p)
            return True
        except Exception as e:
            if not ignore_if_not_exists:
                raise e
            return False

    def cwd(self) -> path:
        """

        :return: the CWD the commands operates in
        """
        return os.path.abspath(self._cwd)

    def get_path(self, p: path) -> path:
        if os.path.isabs(p):
            return os.path.abspath(p)
        else:
            return os.path.abspath(os.path.join(self._cwd, p))

    def ls(self, folder: path = None, generate_absolute_path: bool = False) -> Iterable[path]:
        """
        Show the list of all the files in the given directory

        :param folder: folder to scan. default to CWD
        :param generate_absolute_path: if true, we will generate in the outptu the absolute path of the subfolders.
            Otherwise we will return only the
        :return: iterable of all the files in the given directory
        """
        if folder is None:
            folder = self._cwd
        self._log_command(f"""listing files of folder \"{self.get_path(folder)}\"""")
        yield from self._platform.ls(folder, generate_absolute_path)

    def ls_only_files(self, folder: path = None, generate_absolute_path: bool = False) -> Iterable[path]:
        """
        Show the list of all the files (but not directories) in the given directory

        :param folder: folder to scan. default to CWD
        :param generate_absolute_path: if true, we will generate in the outptu the absolute path of the subfolders. Otherwise we will return only the
        :return:
        """
        if folder is None:
            folder = self._cwd
        p = self.get_path(folder)
        self._log_command(f"""listing files in fodler \"{p}\"""")
        yield from self._platform.ls_only_files(p, generate_absolute_path)

    def ls_only_directories(self, folder: path = None, generate_absolute_path: bool = False) -> Iterable[path]:
        """
        Show the list of all the directories in the given directory

        :param folder: folder to scan. default to CWD
        :param generate_absolute_path: if true, we will generate in the outptu the absolute path of the subfolders. Otherwise we will return only the
        names
        :return:
        """
        if folder is None:
            folder = self._cwd
        p = self.get_path(folder)
        self._log_command(f"""listing folders in folder \"{p}\"""")
        yield from self._platform.ls_only_directories(p, generate_absolute_path)

    def ls_recursive(self, folder: path = None) -> Iterable[path]:
        """
        Show the list of all the files in the given folder

        :param folder: folder to scan (default to cwd)
        :return: list of absolute filename representing the stored files
        """
        self._log_command(f"""listing direct and indirect files of folder \"{self.get_path(folder)}\"""")
        for dirpath, dirnames, filenames in os.walk(folder):
            # dirpath: the cwd wheren dirnames and filesnames are
            # dirnames: list of all the directories in dirpath
            # filenames: list of all the files in dirpath
            for filename in filenames:
                yield self.get_path(os.path.join(dirpath, filename))

    def match(self, string: str, regex: str) -> bool:
        """
        Check if a given string matches perfectly the given regex

        :param string: the sting to check
        :param regex: the regex to check. The syntax is available at https://docs.python.org/3/library/re.html
        :return: true if such a substring can be found, false otherwise
        """
        m = re.match(regex, string)
        return m is not None

    def get_relative_path_wrt(self, p: path, reference: path):
        """
        If we were in folder reference, what actiosn should we perform in order to reach the file p?

        :param p: the file to reach
        :param reference: the folder we are in right now
        :return: relative path
        """
        return os.path.relpath(path=p, start=reference)

    def search(self, string: str, regex: str):
        """
        Check if a given string has a substring that matches the given regex

        :param string: the sting to check
        :param regex: the regex to check. The syntax is available at https://docs.python.org/3/library/re.html
        :return: true if such a substring can be found, false otherwise
        """
        m = re.match(regex, string)
        return m is not None

    def ls_directories_recursive(self, folder: path) -> Iterable[path]:
        """
        Show the list of all the directories in the given folder

        :param folder: folder to scan (default to cwd)
        :return: list of absolute filename representing the stored directories
        """
        self._log_command(f"""listing direct and indirect folders of folder \"{self.get_path(folder)}\"""")
        for dirpath, dirnames, filenames in os.walk(folder):
            # dirpath: the cwd wheren dirnames and filesnames are
            # dirnames: list of all the directories in dirpath
            # filenames: list of all the files in dirpath
            for dirname in dirnames:
                yield self.get_path(os.path.join(dirpath, dirname))

    def cd(self, folder: path, create_if_not_exists: bool = True) -> path:
        """
        Gain access to a directory. If the directory does nto exists, it is created
        If the path is relative, it is relative to the CWD

        :param folder: folder where we need to go into
        :param create_if_not_exists: if true, we will create the directory if we try to cd into a non existent directory
        :return: the directory where we have cd from
        """
        result = self.cwd()
        self._log_command(f"""cd into folder \"{self.get_path(folder)}\"""")
        self._cwd = self.get_path(folder)
        if not os.path.exists(self._cwd) and create_if_not_exists:
            os.makedirs(self._cwd, exist_ok=True)
        return result

    def current_user(self) -> str:
        """
        get the user currently logged

        :return: the user currently logged
        """
        return self._platform.get_current_username()

    def abs_wrt_cwd(self, *paths) -> path:
        """
        generate a path relative to cwd and generate the absolute path of it

        :param paths: the single elements of a path to join and whose absolute path we need to compute
        :return: absolute path, relative to the current working directory
        """
        return os.path.abspath(os.path.join(self._cwd, *paths))

    def make_directories(self, folder: path) -> None:
        """
        Create all the needed directories for the given path
        :param folder: folders to create
        """
        self._log_command(f"""Recursively create directories \"{self.get_path(folder)}\"""")
        os.makedirs(self.get_path(folder), exist_ok=True)

    def cd_into_directories(self, folder: path, prefix: str, folder_format: str, error_if_mismatch: bool = True):
        """
        Inside the given folder, there can be several folders, each of them with the same format. We cd into the "latest" one.
        How can we determine which is the "latest" one? Via folder_format. it is a string that is either:
        - "number": an integer number
        - "semver2": a semantic versionign string;
        We fetch the "latest" by looking at the one with the greater value. If the folder contains a folder which it is not compliant
        with folder_format, it is either ignored or rase error

        :param folder: folder where several folders are located
        :param prefix: a string that prefix folder_format
        :param folder_format: either "number" or "semver2"
        :param error_if_mismatch: if a folder is not compliant with folder_format, if true we will generate an exception
        :return:
        """

        try:
            p = self.get_path(folder)
            self._log_command(f"Cd'ing into the \"latest\" directory in folder \"{p}\" according to criterion \"{folder_format}\"")
            self._disable_log_command = True
            self.cd(folder)

            folders = dict()
            for subfolder in self.ls_only_directories(p):
                if not subfolder.startswith(prefix):
                    if error_if_mismatch:
                        raise PMakeException(f"subfolder \"{subfolder}\" in \"{p}\" does not start with \"{prefix}\"")
                    else:
                        continue

                subfolder_id = subfolder[len(prefix):]
                try:
                    if folder_format == "semver2":
                        folders[semver.VersionInfo.parse(subfolder_id)] = subfolder
                    elif folder_format == "number":
                        folders[int(subfolder_id)] = subfolder
                    else:
                        raise InvalidScenarioPMakeException(f"invalid folder_format \"{folder_format}\"")
                except Exception as e:
                    if error_if_mismatch:
                        raise e
                    else:
                        continue

            # fetch the "latest" by fetching the greater value in "folders"
            latest_folder = list(sorted(folders.keys()))[0]
            self.cd(folders[latest_folder])
        finally:
            self._disable_log_command = False

    def create_temp_directory_with(self, directory_prefix: str) -> Any:
        """
        Create a temporary directory on the file system where to put temporary files

        :param directory_prefix: a prefix to be put before the temporary folder
        :return: a value which can be the input of a "with" statement. The folder will be automatically removed at the
        end of the with. You can access the directory filename via the result field "name"
        """
        return self._platform.create_temp_directory_with(directory_prefix)

    def create_temp_file(self, directory: str, file_prefix: str = None, file_suffix: str = None, mode: str = "r",
                         encoding: str = "utf-8", readable_for_all: bool = False, executable_for_owner: bool = False,
                         executable_for_all: bool = False) -> Tuple[Any, str]:
        """
        Creates the file. You need to manually dispose of the file by yourself

        :param directory: the directory where to put the file
        :param file_prefix: a string that will be put at the beginning of the filename
        :param file_suffix: a string that will be put at the end of the filename
        :param mode: how we will open the file. E.g., "r", "w"
        :param encoding: the encodign of the file. Default to "utf-8"
        :param readable_for_all: if True, the file can be read by anyone
        :param executable_for_owner: if True, the file can be executed by the owner
        :param executable_for_all: if True, anyone can execute the file
        :return: a tuple where the first item is the object you can use to read/write into the file. The second element
            is the absolute path of the temp file
        """
        return self._platform.create_temp_file(
            directory=directory,
            file_prefix=file_prefix,
            file_suffix=file_suffix,
            mode=mode,
            encoding=encoding,
            readable_for_all=readable_for_all,
            executable_for_owner=executable_for_owner,
            executable_for_all=executable_for_all
        )

    def execute_and_forget(self, commands: Union[str, List[Union[str, List[str]]]], cwd: path = None, env: Dict[str, str] = None, check_exit_code: bool = True, timeout: int = None) -> int:
        """
        Execute a command but ensure that no stdout will be printed on the console

        :param commands: the command to execute. They will be exeucte in the same context
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured), the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        result, _, _ = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=False,
            capture_stdout=False,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=False,
            admin_password=None,
            log_entry=True,
        )
        return result

    def execute_stdout_on_screen(self, commands: Union[str, List[Union[str, List[str]]]], cwd: path = None,
                                 env: Dict[str, Any] = None, check_exit_code: bool = True, timeout: int = None) -> int:
        """
        Execute a command. We won't capture the stdout but we will show it on pmake console

        :param commands: the command to execute. They will be exeucte in the same context
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured), the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        result, _, _ = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=True,
            capture_stdout=False,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=False,
            admin_password=None,
            log_entry=True,
        )
        return result

    def execute_return_stdout(self, commands: Union[str, List[Union[str, List[str]]]], cwd: path = None,
                              env: Dict[str, Any] = None,
                              check_exit_code: bool = True, timeout: int = None) -> Tuple[int, str, str]:
        """
        Execute a command. We won't show the stdout on pmake console but we will capture it and returned it

        :param commands: the command to execute. They will be exeucte in the same context
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured), the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        exit_code, stdout, stderr = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=False,
            capture_stdout=True,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=False,
            admin_password=None,
            log_entry=True
        )
        return exit_code, stdout, stderr

    def execute_admin_and_forget(self, commands: Union[str, List[Union[str, List[str]]]], cwd: path = None,
                                 env: Dict[str, Any] = None,
                                 check_exit_code: bool = True, timeout: int = None) -> int:
        """
        Execute a command as admin but ensure that no stdout will be printed on the console

        :param commands: the command to execute. They will be exeucte in the same context
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured), the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        result, _, _ = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=False,
            capture_stdout=False,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=True,
            admin_password=None,
            log_entry=True,
        )
        return result

    def execute_admin_stdout_on_screen(self, commands: Union[str, List[Union[str, List[str]]]], cwd: path = None,
                                       env: Dict[str, Any] = None,
                                       check_exit_code: bool = True, timeout: int = None) -> int:
        """
        Execute a command as an admin. We won't capture the stdout but we will show it on pmake console

        :param commands: the command to execute. They will be execute in the same context
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured),
            the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        result, _, _ = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=True,
            capture_stdout=False,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=True,
            admin_password=None,
            log_entry=True,
        )
        return result

    def execute_admin_return_stdout(self, commands: Union[str, List[Union[str, List[str]]]], cwd: path = None,
                                    env: Dict[str, Any] = None,
                                    check_exit_code: bool = True, timeout: int = None) -> Tuple[int, str, str]:
        """
        Execute a command as an admin. We won't show the stdout on pmake console but we will capture it and returned it

        :param commands: the command to execute. They will be execute in the same context
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured),
            the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        exit_code, stdout, stderr = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=False,
            capture_stdout=True,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=True,
            admin_password=None,
            log_entry=True
        )
        return exit_code, stdout, stderr

    def execute_admin_with_password_fire_and_forget(self, commands: Union[str, List[Union[str, List[str]]]],
                                                    password: str,
                                                    cwd: str = None, env: Dict[str, Any] = None,
                                                    check_exit_code: bool = True, timeout: int = None) -> int:
        """
        Execute a command as admin by providing the admin password. **THIS IS INCREDIBLE UNSAFE!!!!!!!!!!!!**.
        Please, I beg you, do **NOT** use this if you need any level of security!!!!! This will make the password visible
        on top, on the history, everywhere on your system. Please use it only if you need to execute a command on your
        local machine.

        :param commands: the command to execute. They will be executed in the same context
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :param password: **[UNSAFE!!!!]** If you **really** need, you might want to run a command as an admin
            only on your laptop, and you want a really quick and dirty way to execute it, like as in the shell.
            Do **not** use this in production code, since the password will be 'printed in clear basically everywhere!
            (e.g., history, system monitor, probably in a file as well)
        """
        if cwd is None:
            cwd = self.cwd()
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        result, _, _ = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=False,
            capture_stdout=False,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=True,
            admin_password=password,
            log_entry=True,
        )
        return result

    def execute_admin_with_password_stdout_on_screen(self, commands: Union[str, List[Union[str, List[str]]]], password: str, cwd: path = None, env: Dict[str, Any] = None, check_exit_code: bool = True, timeout: int = None) -> int:
        """
        Execute a command as an admin. We won't capture the stdout but we will show it on pmake console

        :param commands: the command to execute. They will be execute in the same context
        :param password: **[UNSAFE!!!!]** If you **really** need, you might want to run a command as an admin
            only on your laptop, and you want a really quick and dirty way to execute it, like as in the shell.
            Do **not** use this in production code, since the password will be 'printed in clear basically everywhere!
            (e.g., history, system monitor, probably in a file as well)
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured),
            the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        result, _, _ = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=True,
            capture_stdout=False,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=True,
            admin_password=password,
            log_entry=True,
        )
        return result

    def execute_admin_with_password_return_stdout(self, commands: Union[str, List[Union[str, List[str]]]],
                                                  password: str, cwd: path = None, env: Dict[str, Any] = None,
                                                  check_exit_code: bool = True,
                                                  timeout: int = None) -> Tuple[int, str, str]:
        """
        Execute a command as an admin. We won't show the stdout on pmake console but we will capture it and returned it

        :param commands: the command to execute. They will be execute in the same context
        :param password: **[UNSAFE!!!!]** If you **really** need, you might want to run a command as an admin
            only on your laptop, and you want a really quick and dirty way to execute it, like as in the shell.
            Do **not** use this in production code, since the password will be 'printed in clear basically everywhere!
            (e.g., history, system monitor, probably in a file as well)
        :param cwd: current working directory where the command is executed
        :param env: a dictionary representing the key-values of the environment variables
        :param check_exit_code: if true, we will generate an exception if the exit code is different than 0
        :param timeout: if positive, we will give up waiting for the command after the amount of seconds
        :return: triple. The first element is the error code, the second is the stdout (if captured),
            the third is stderr
        """
        if cwd is None:
            cwd = self._cwd
        else:
            cwd = self.get_path(cwd)

        if isinstance(commands, str):
            commands = [commands]

        exit_code, stdout, stderr = self._platform.execute_command(
            commands=commands,
            show_output_on_screen=False,
            capture_stdout=True,
            cwd=cwd,
            env=env,
            check_exit_code=check_exit_code,
            timeout=timeout,
            execute_as_admin=True,
            admin_password=password,
            log_entry=True
        )
        return exit_code, stdout, stderr

    def include_string(self, string: str):
        self._model.execute_string(string)

    def include_file(self, file: path):
        """
        Replace the include directive with the content fo the included file. Fails if there is no such path

        :param file: the external file to include in the script
        :return:
        """

        p = self.get_path(file)
        self._log_command(f"include file content \"{p}\"")
        self._model.execute_file(p)


