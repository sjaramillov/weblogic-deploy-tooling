"""
Copyright (c) 2018, Oracle and/or its affiliates. All rights reserved.
The Universal Permissive License (UPL), Version 1.0
"""
import copy
import os
import re

import java.lang.IllegalArgumentException as IllegalArgumentException

import oracle.weblogic.deploy.aliases.AliasException as AliasException
import oracle.weblogic.deploy.json.JsonException
import oracle.weblogic.deploy.util.VariableException as VariableException

import wlsdeploy.util.model as model_sections
import wlsdeploy.util.variables as variables
from wlsdeploy.aliases.aliases import Aliases
from wlsdeploy.aliases.location_context import LocationContext
from wlsdeploy.aliases.wlst_modes import WlstModes
from wlsdeploy.aliases.validation_codes import ValidationCodes
from wlsdeploy.json.json_translator import JsonToPython
from wlsdeploy.logging.platform_logger import PlatformLogger
from wlsdeploy.util.model_translator import FileToPython

VARIABLE_INJECTOR_FILE_NAME = 'model_variable_injector.json'
VARIABLE_KEYWORDS_FILE_NAME = 'variable_keywords.json'
VARIABLE_INJECTOR_PATH_NAME_ARG = 'variable_injector_path_name'
VARIABLE_KEYWORDS_PATH_NAME_ARG = 'variable_keywords_path_name'
VARIABLE_INJECTOR_FILE_NAME_ARG = 'variable_injector_file_name'
VARIABLE_KEYWORDS_FILE_NAME_ARG = 'variable_keywords_file_name'
VARIABLE_FILE_NAME_ARG = 'variable_file_name'
VARIABLE_FILE_NAME = 'variables.json'
# custom keyword in model injector file
CUSTOM_KEYWORD = 'CUSTOM'
KEYWORD_FILES = 'file-list'
# location for model injector file, keyword file and injector files
DEFAULT_FILE_LOCATION = 'lib'
# should these injector json keywords be included in the keyword file
REGEXP = 'regexp'
SUFFIX = 'suffix'
FORCE = 'force'

VARIABLE_SEP = '.'
SUFFIX_SEP = '--'
_variable_sep_pattern = re.compile('/')

_wlsdeploy_location = os.environ.get('WLSDEPLOY_HOME')
_segment_pattern = re.compile("\\[[\w.-]+\\]$")
_class_name = 'variable_file_helper'
_logger = PlatformLogger('wlsdeploy.util')


class VariableFileHelper(object):

    def __init__(self, model, model_context=None, version=None):
        self.__original = copy.deepcopy(model)
        self.__model = dict()
        # consolidate all the model sections into one for speedier search
        # currently don't want to do variable injection in the domainInfo section of the model
        if model_sections.get_model_topology_key() in model:
            self.__model.update(model[model_sections.get_model_topology_key()])
        elif model_sections.get_model_resources_key() in model:
            self.__model.update(model_sections.get_model_resources_key())
        elif model_sections.get_model_deployments_key() in model:
            self.__model.update(model_sections.get_model_deployments_key())
        if version:
            self.__aliases = Aliases(model_context, WlstModes.OFFLINE, version, None)
        else:
            self.__aliases = Aliases(model_context)

    def inject_variables_keyword_file(self, **kwargs):
        """
        Replace attribute values with variables and generate a variable dictionary.
        The variable replacement is driven from the values in the model variable helper file.
        This file can either contain the name of a replacement file, or a list of pre-defined
        keywords for canned replacement files.
        Return the variable dictionary with the variable name inserted into the model, and the value
        that the inserted variable replaced.
        :param kwargs: arguments used to override default for variable processing, typically used in test situations
        :return: variable dictionary containing
        """
        _method_name = 'inject_variables_keyword_file'
        _logger.entering(class_name=_class_name, method_name=_method_name)

        variable_injector_location_file = _get_variable_injector_file_name(**kwargs)
        variables_injector_dictionary = _load_variables_file(variable_injector_location_file)
        variable_keywords_location_file = _get_variable_keywords_file_name(**kwargs)
        keywords_dictionary = _load_keywords_file(variable_keywords_location_file)

        variables_inserted = False
        return_model = dict()
        variable_file_location = None
        if variables_injector_dictionary and keywords_dictionary:
            injector_file_list = _create_injector_file_list(variables_injector_dictionary, keywords_dictionary,
                                                            _get_keyword_files_location(**kwargs))
            return_model = dict()
            variable_file_location = _get_variable_file_name(variables_injector_dictionary, **kwargs)
            if not variable_file_location:
                _logger.warning('WLSDPLY-19420', variable_injector_location_file, class_name=_class_name,
                                method_name=_method_name)
            else:
                variables_file_dictionary = self.inject_variables_keyword_dictionary(injector_file_list)
                variables_inserted = _write_variables_file(variables_file_dictionary, variable_file_location)
                if variables_inserted:
                    _logger.info('WLSDPLY-19418', variable_file_location, class_name=_class_name,
                                 method_name=_method_name)
                    return_model = self.__model
                else:
                    _logger.fine('WLSDPLY-19419', class_name=_class_name, method_name=_method_name)
                    return_model = self.__original
                    variable_file_location = None

        _logger.exiting(class_name=_class_name, method_name=_method_name, result=variables_inserted)
        return variables_inserted, return_model, variable_file_location

    def inject_variables_keyword_dictionary(self, injector_file_list):
        """
        Takes a variable keyword dictionary and returns a variables for file in a dictionary
        :param injector_file_list:
        :return:
        """
        _method_name = 'inject_variables_keyword_dictionary'
        _logger.entering(injector_file_list, class_name=_class_name, method_name=_method_name)
        variables_dictionary = dict()
        for filename in injector_file_list:
            injector_dictionary = _load_injector_file(filename)
            entries = self.inject_variables(injector_dictionary)
            if entries:
                _logger.finer('WLSDPLY-19413', filename, class_name=_class_name, method_name=_method_name)
                variables_dictionary.update(entries)
        _logger.exiting(class_name=_class_name, method_name=_method_name, result=variables_dictionary)
        return variables_dictionary

    def inject_variables(self, injector_dictionary):
        """
        Iterate through the injector dictionary that was loaded from the file for the model
        injector file keyword.
        :param injector_dictionary:
        :return: variable dictionary containing the variable string and model value entries
        """
        variable_dict = dict()
        if injector_dictionary:
            location = LocationContext()
            domain_token = self.__aliases.get_name_token(location)
            location.add_name_token(domain_token, 'fakedomain')
            for injector in injector_dictionary:
                entries_dict = self.__inject_variable(location, injector)
                if len(entries_dict) > 0:
                    variable_dict.update(entries_dict)

        return variable_dict

    def __inject_variable(self, location, injector):
        _method_name = '__inject_variable'
        _logger.entering(injector, class_name=_class_name, method_name=_method_name)
        variable_dict = dict()
        start_mbean_list, attribute = _split_injector(injector)

        def _traverse_variables(model_section, mbean_list):
            if mbean_list:
                mbean = mbean_list.pop(0)
                # mbean, mbean_name_list = _find_special_name(mbean)
                if mbean in model_section:
                    _logger.finest('WLSDPLY-19414', mbean, class_name=_class_name, method_name=_method_name)
                    next_model_section = model_section[mbean]
                    location.append_location(mbean)
                    name_token = self.__aliases.get_name_token(location)
                    # if not mbean_name_list and self.__aliases.supports_multiple_mbean_instances(location):
                    #     mbean_name_list = next_model_section
                    # if mbean_name_list:
                    #     for mbean_name in mbean_name_list:
                    if self.__aliases.supports_multiple_mbean_instances(location):
                        for mbean_name in next_model_section:
                            continue_mbean_list = copy.copy(mbean_list)
                            location.add_name_token(name_token, mbean_name)
                            _traverse_variables(next_model_section[mbean_name], continue_mbean_list)
                            location.remove_name_token(name_token)
                    else:
                        _traverse_variables(next_model_section, mbean_list)
                    location.pop_location()
                else:
                    self._log_mbean_not_found(mbean, injector[0], location)
                    return False
            else:
                if attribute in model_section:
                    variable_name, variable_value = self._variable_info(model_section, attribute, location, injector)
                    if variable_value:
                        variable_dict[variable_name] = variable_value
                else:
                    _logger.finer('WLSDPLY-19417', attribute, injector, location.get_folder_path(),
                                  class_name=_class_name, method_name=_method_name)
            return True

        _traverse_variables(self.__model, start_mbean_list)
        _logger.exiting(class_name=_class_name, method_name=_method_name, result=variable_dict)
        return variable_dict

    def __format_variable_name(self, location, attribute):
        path = ''
        make_path = self.__aliases.get_model_folder_path(location)
        if make_path:
            make_path = make_path.split(':')
            if len(make_path) > 1 and len(make_path[1]) > 1:
                path = make_path[1]
                path = path[1:] + VARIABLE_SEP + attribute
        _variable_sep_pattern.sub(VARIABLE_SEP, path)
        return path

    def __format_variable_name_segment(self, location, attribute, suffix):
        path = self.__format_variable_name(location, attribute)
        return path + SUFFIX_SEP + suffix

    def _variable_info(self, model, attribute, location, injector):
        if REGEXP in injector:
            return self._process_regexp(model, attribute, location, injector)
        else:
            return self._process_attribute(model, attribute, location, injector)

    def _process_attribute(self, model, attribute, location, injector):
        _method_name = '_process_attribute'
        _logger.entering(attribute, location.get_folder_path(), injector, class_name=_class_name,
                         method_name=_method_name)
        variable_name = None
        variable_value = None
        attribute_value = model[attribute]
        if not _already_property(attribute_value):
            variable_name = self.__format_variable_name(location, attribute)
            variable_value = str(model[attribute])
            model[attribute] = _format_as_property(variable_name)
        else:
            _logger.finer('WLSDPLY-19426', attribute_value, attribute, str(location), class_name=_class_name,
                          method_name=_method_name)

        _logger.exiting(class_name=_class_name, method_name=_method_name, result=variable_value)
        return variable_name, variable_value

    def _process_regexp(self, model, attribute, location, injector):
        regexp = injector[REGEXP]
        suffix = None
        if SUFFIX in injector:
            suffix = injector[SUFFIX]
        if isinstance(model[attribute], dict):
            return self._process_regexp_dictionary(attribute, model[attribute], location, regexp, suffix)
        elif type(model[attribute]) == list:
            return self._process_regexp_list(attribute, model[attribute], location, regexp, suffix)
        else:
            return self._process_regexp_string(model, attribute, location, regexp, suffix)

    def _process_regexp_string(self, model, attribute, location, regexp, suffix):
        _method_name = '_process_regexp_string'
        _logger.entering(attribute, location.get_folder_path(), regexp, suffix, class_name=_class_name,
                         method_name=_method_name)
        attribute_value, variable_name, variable_value = self._find_segment_in_string(attribute, model[attribute],
                                                                                      regexp, suffix, location)
        if variable_value:
            _logger.finer('WLSDPLY-19429', attribute, attribute_value, class_name=_class_name,
                          method_name=_method_name)
            model[attribute] = attribute_value
        # elif replace_if_nosegment:
        #     check_value = model[attribute]
        #     if not _already_property(check_value):
        #         variable_value = check_value
        #         variable_name = self.__format_variable_name(location, attribute)
        #         model[attribute] = _format_as_property(variable_name)
        #         _logger.finer('WLSDPLY-19430', attribute, model[attribute], class_name=_class_name,
        #                       method_name=_method_name)
        else:
            _logger.finer('WLSDPLY-19424', regexp, attribute, model[attribute],
                          location.get_folder_path, class_name=_class_name,
                          method_name=_method_name)
        _logger.exiting(class_name=_class_name, method_name=_method_name, result=variable_value)
        return variable_name, variable_value

    def _find_segment_in_string(self, attribute, attribute_value, regexp, suffix, location):
        variable_name = None
        variable_value = None
        if not _already_property(attribute_value):
            variable_name = self.__format_variable_name_segment(location, attribute, suffix)
            attribute_value, variable_value = _replace_segment(regexp, str(attribute_value),
                                                               _format_as_property(variable_name))
        return attribute_value, variable_name, variable_value

    def _process_regexp_list(self, attribute_name, attribute_list, regexp, location, suffix):
        _method_name = '_process_regexp_list'
        _logger.entering(attribute_name, attribute_list, regexp, location.get_folder_path(), suffix,
                         class_name=_class_name, method_name=_method_name)
        variable_name = None
        variable_value = None
        idx = 0
        for entry in attribute_list:
            attribute_value, seg_var_name, seg_var_value = self._find_segment_in_string(attribute_name, entry, regexp,
                                                                                        suffix, location)
            if seg_var_value:
                _logger.finer('WLSDPLY-19429', attribute_name, attribute_value, class_name=_class_name,
                              method_name=_method_name)
                attribute_list[idx] = attribute_value
                variable_name = seg_var_name
                variable_value = seg_var_value

            idx += 1
            # don't break, continue replacing any in dictionary, return the last variable value found
        _logger.exiting(class_name=_class_name, method_name=_method_name, result=variable_value)
        return variable_name, variable_value

    def _process_regexp_dictionary(self, attribute_name, attribute_dict, location, regexp, suffix):
        _method_name = '_process_regexp_dictionary'
        _logger.entering(attribute_name, attribute_dict, location.get_folder_path(), regexp, suffix,
                         class_name=_class_name, method_name=_method_name)
        variable_name = self.__format_variable_name_segment(location, attribute_name, suffix)
        variable_value = None
        replacement = _format_as_property(variable_name)
        for entry in attribute_dict:
            if not _already_property(attribute_dict[entry]):
                matcher = re.search(suffix, entry)
                if matcher:
                    _logger.finer('WLSDPLY-19427', attribute_name, replacement, class_name=_class_name,
                                  method_name=_method_name)
                    variable_value = str(attribute_dict[entry])
                    attribute_dict[entry] = replacement
                    # don't break, continue replacing any in dictionary, return the last variable value found
        _logger.exiting(class_name=_class_name, method_name=_method_name, result=variable_value)
        return variable_name, variable_value

    def _log_mbean_not_found(self, mbean, replacement, location):
        _method_name = '_log_mbean_not_found'
        code = ValidationCodes.INVALID
        try:
            code, __ = self.__aliases.is_valid_model_folder_name(location, mbean)
        except AliasException, ae:
            _logger.fine('AliasException {0}', ae.getLocalizedMessage())
            pass
        if code == ValidationCodes.INVALID:
            _logger.warning('WLSDPLY-19415', mbean, replacement, location.get_folder_path(),
                            class_name=_class_name, method_name=_method_name)
        else:
            _logger.finer('WLSDPLY-19416', mbean, replacement, location.get_folder_path(),
                          class_name=_class_name, method_name=_method_name)


def _get_variable_file_name(variables_injector_dictionary, **kwargs):
    if VARIABLE_FILE_NAME_ARG in variables_injector_dictionary:
        variable_file_location = variables_injector_dictionary[VARIABLE_FILE_NAME_ARG]
        del variables_injector_dictionary[VARIABLE_FILE_NAME_ARG]
        _logger.finer('WLSDPLY-19422', variable_file_location)
    elif VARIABLE_FILE_NAME_ARG in kwargs:
        variable_file_location = kwargs[VARIABLE_FILE_NAME_ARG]
        _logger.finer('WLSDPLY-19421', variable_file_location)
    else:
        variable_file_location = None
    return variable_file_location


def _get_variable_injector_file_name(**kwargs):
    variable_injector_file_name = VARIABLE_INJECTOR_FILE_NAME
    if VARIABLE_INJECTOR_FILE_NAME_ARG in kwargs:
        variable_injector_file_name = kwargs[VARIABLE_INJECTOR_FILE_NAME_ARG]
    if VARIABLE_INJECTOR_PATH_NAME_ARG in kwargs:
        return os.path.join(kwargs[VARIABLE_INJECTOR_PATH_NAME_ARG], variable_injector_file_name)
    else:
        return os.path.join(_wlsdeploy_location, DEFAULT_FILE_LOCATION, variable_injector_file_name)


def _get_variable_keywords_file_name(**kwargs):
    variable_keywords_file_name = VARIABLE_KEYWORDS_FILE_NAME
    if VARIABLE_KEYWORDS_FILE_NAME_ARG in kwargs:
        variable_keywords_file_name = kwargs[VARIABLE_KEYWORDS_FILE_NAME_ARG]
    if VARIABLE_KEYWORDS_PATH_NAME_ARG in kwargs:
        return os.path.join(kwargs[VARIABLE_KEYWORDS_PATH_NAME_ARG], variable_keywords_file_name)
    else:
        return os.path.join(_wlsdeploy_location, DEFAULT_FILE_LOCATION, variable_keywords_file_name)


def _load_variables_file(variable_injector_location):
    _method_name = '_load_variables_dictionary'
    _logger.entering(variable_injector_location, class_name=_class_name, method_name=_method_name)
    variables_dictionary = None
    if os.path.isfile(variable_injector_location):
        try:
            variables_dictionary = FileToPython(variable_injector_location).parse()
            _logger.fine('WLSDPLY-19400', variable_injector_location, class_name=_class_name, method_name=_method_name)
        except IllegalArgumentException, ia:
            _logger.warning('WLSDPLY-19402', variable_injector_location, ia.getLocalizedMessage(),
                            class_name=_class_name, method_name=_method_name)
    _logger.exiting(class_name=_class_name, method_name=_method_name, result=variables_dictionary)
    return variables_dictionary


def _load_keywords_file(variable_keywords_location):
    _method_name = '_load_keywords_dictionary'
    _logger.entering(variable_keywords_location, class_name=_class_name, method_name=_method_name)
    keywords_dictionary = None
    if os.path.isfile(variable_keywords_location):
        try:
            keywords_dictionary = FileToPython(variable_keywords_location).parse()
            _logger.fine('WLSDPLY-19432', variable_keywords_location, class_name=_class_name, method_name=_method_name)
        except IllegalArgumentException, ia:
            _logger.warning('WLSDPLY-19433', variable_keywords_location, ia.getLocalizedMessage(),
                            class_name=_class_name, method_name=_method_name)

    _logger.exiting(class_name=_class_name, method_name=_method_name, result=keywords_dictionary)
    return keywords_dictionary


def _create_injector_file_list(variables_dictionary, keyword_dictionary, injector_path):
    _method_name = '_create_file_dictionary'
    injector_file_list = []
    if CUSTOM_KEYWORD in variables_dictionary:
        if KEYWORD_FILES in variables_dictionary[CUSTOM_KEYWORD]:
            file_list = variables_dictionary[CUSTOM_KEYWORD][KEYWORD_FILES]
            if type(file_list) != list:
                file_list = file_list.split(',')
            for filename in file_list:
                injector_file_list.append(filename)
        else:
            _logger.info('WLSDPLY-19434', class_name=_class_name, method_name=_method_name)
        del variables_dictionary[CUSTOM_KEYWORD]
    for keyword in variables_dictionary:
        if keyword in keyword_dictionary:
            filename = keyword_dictionary[keyword]
            if filename and filename not in injector_file_list:
                if not os.path.isabs(filename):
                    filename = os.path.join(injector_path, filename)
                injector_file_list.append(filename)
                _logger.finer('WLSDPLY-19408', filename, keyword)
        else:
            _logger.warning('WLSDPLY-19403', keyword, class_name=_class_name, method_name=_method_name)
    return injector_file_list


def _get_keyword_files_location(**kwargs):
    if VARIABLE_INJECTOR_PATH_NAME_ARG in kwargs:
        return kwargs[VARIABLE_INJECTOR_PATH_NAME_ARG]
    else:
        return _wlsdeploy_location


def _load_injector_file(injector_file_name):
    _method_name = '_load_injector_file'
    _logger.entering(injector_file_name, class_name=_class_name, method_name=_method_name)
    injector_dictionary = dict()
    if os.path.isfile(injector_file_name):
        try:
            injector_dictionary = JsonToPython(injector_file_name).parse()
        except oracle.weblogic.deploy.json, je:
            _logger.warning('WLDPLY-19409', injector_file_name, je.getLocalizedMessage(), class_name=_class_name,
                            method_name=_method_name)
    else:
        _logger.warning('WLSDPLY-19410', injector_file_name, class_name=_class_name, method_name=_method_name)

    _logger.exiting(class_name=_class_name, method_name=_method_name)
    return injector_dictionary


def _replace_segment(regexp, variable_value, attribute_value):
    replaced_value = None
    replacement_string = variable_value
    pattern = re.compile(regexp)
    matcher = pattern.search(variable_value)
    if matcher:
        replaced_value = variable_value[matcher.start():matcher.end()]

        replacement_string = pattern.sub(attribute_value, variable_value)
    return replacement_string, replaced_value


def _already_property(check_string):
    return type(check_string) == str and check_string.startswith('@@PROP:')


def _format_as_property(prop_name):
    return '@@PROP:%s@@' % prop_name


def _split_injector(injector_path):
    """
    Split the injector path into an mbean list and an attribute name from the injector path string
    :param injector_path:
    :return: attribute name:mbean list of mbean folder nodes
    """
    attr = None
    ml = injector_path.split('.')
    if len(ml) > 0:
        attr = ml.pop()
    return ml, attr


def _find_special_name(mbean):
    mbean_name = mbean
    mbean_name_list = []
    name_list = re.split('[\{).+\}]', mbean)
    if name_list and len(name_list) > 1:
        mbean_name = name_list[0]
        mbean_name_list = name_list[1].split(',')
    return mbean_name, mbean_name_list


def _write_variables_file(variables_dictionary, variables_file_name):
    _method_name = '_write_variables_file'
    _logger.entering(variables_dictionary, variables_file_name, class_name=_class_name, method_name=_method_name)
    written = False
    if variables_dictionary:
        try:
            variables.write_variables(variables_dictionary, variables_file_name)
            written = True
        except VariableException, ve:
            _logger.warning('WLSDPLY-19407', variables_file_name, ve.getLocalizedMessage(), class_name=_class_name,
                            method_name=_method_name)
    _logger.exiting(class_name=_class_name, method_name=_method_name, result=written)
    return written
