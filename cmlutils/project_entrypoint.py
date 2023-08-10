import logging
import os
import sys
from configparser import ConfigParser, NoOptionError
from json import dump
from logging.handlers import RotatingFileHandler

import click

from cmlutils import constants
from cmlutils.constants import (
    API_V1_KEY,
    CA_PATH_KEY,
    OUTPUT_DIR_KEY,
    URL_KEY,
    USERNAME_KEY,
)
from cmlutils.directory_utils import get_project_metadata_file_path
from cmlutils.projects import ProjectExporter, ProjectImporter
from cmlutils.script_models import ValidationResponseStatus
from cmlutils.utils import get_absolute_path, parse_runtimes_v2, read_json_file
from cmlutils.validator import (
    initialize_export_validators,
    initialize_import_validators,
)


def _configure_project_command_logging(log_filedir: str, project_name: str):
    os.makedirs(name=log_filedir, exist_ok=True)
    log_filename = log_filedir + constants.LOG_FILE
    logging.basicConfig(
        handlers=[
            logging.StreamHandler(sys.stdout),
            RotatingFileHandler(
                filename=log_filename, maxBytes=10000000, backupCount=5
            ),
        ],
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(custom_attribute)s - %(message)s",
        datefmt="%d/%m/%Y %H:%M:%S",
    )
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.custom_attribute = project_name
        return record

    logging.setLogRecordFactory(record_factory)


def _read_config_file(file_path: str, project_name: str):
    output_config = {}
    config = ConfigParser()
    if os.path.exists(file_path):
        config.read(file_path)
        keys = (USERNAME_KEY, URL_KEY, API_V1_KEY, OUTPUT_DIR_KEY)
        for key in keys:
            try:
                value = config.get(project_name, key)
                output_config.setdefault(key, value)
            except NoOptionError:
                print("Key %s is missing from config file." % (key))
                raise
        output_config[CA_PATH_KEY] = config.get(project_name, CA_PATH_KEY, fallback="")
        return output_config
    else:
        print("Validation error: cannot find config file:", file_path)
        raise RuntimeError("validation error", "Cannot find config file")


@click.group(name="project")
def project_cmd():
    """
    Sub-entrypoint for project command
    """


@project_cmd.command(name="export")
@click.option(
    "--project_name",
    "-p",
    help="Name of the project to be migrated. Make sure the name matches with the section name in export-config.ini file",
    required=True,
)
def project_export_cmd(project_name):
    pexport = None
    config = _read_config_file(
        os.path.expanduser("~") + "/.cmlutils/export-config.ini", project_name
    )

    username = config[USERNAME_KEY]
    url = config[URL_KEY]
    apiv1_key = config[API_V1_KEY]
    output_dir = config[OUTPUT_DIR_KEY]
    ca_path = config[CA_PATH_KEY]

    output_dir = get_absolute_path(output_dir)
    ca_path = get_absolute_path(ca_path)

    log_filedir = os.path.join(output_dir, project_name, "logs")
    _configure_project_command_logging(log_filedir, project_name)
    logging.info("Started exporting project: %s", project_name)
    try:
        # Get username of the creator of project - This is required so that admins can also migrate the project
        pobj = ProjectExporter(
            host=url,
            username=username,
            project_name=project_name,
            api_key=apiv1_key,
            top_level_dir=output_dir,
            ca_path=ca_path,
            project_slug=project_name,
            owner_type="",
        )
        creator_username, project_slug, owner_type = pobj.get_creator_username()
        if creator_username is None:
            logging.error(
                "Validation error: Cannot find project - %s under username %s",
                project_name,
                username,
            )
            raise RuntimeError("Validation error")
        logging.info("Begin validating for export.")
        validators = initialize_export_validators(
            host=url,
            username=creator_username,
            project_name=project_name,
            top_level_directory=output_dir,
            apiv1_key=apiv1_key,
            ca_path=ca_path,
            project_slug=project_slug,
        )
        for v in validators:
            validation_response = v.validate()
            if validation_response.validation_status == ValidationResponseStatus.FAILED:
                logging.error(
                    "Validation error: %s",
                    project_name,
                    validation_response.validation_msg,
                )
                raise RuntimeError(
                    "validation error", validation_response.validation_msg
                )
        logging.info(
            "Finished validating export validations for project %s.", project_name
        )
        logging.info("File transfer has started.")
        pexport = ProjectExporter(
            host=url,
            username=creator_username,
            project_name=project_name,
            api_key=apiv1_key,
            top_level_dir=output_dir,
            ca_path=ca_path,
            project_slug=project_slug,
            owner_type=owner_type,
        )
        pexport.transfer_project_files()
        pexport.dump_project_and_related_metadata()
    except:
        logging.error("Exception:", exc_info=1)
        if pexport:
            pexport.terminate_ssh_session()
        exit()


@project_cmd.command(name="import")
@click.option(
    "--project_name",
    "-p",
    help="Name of the project to be migrated. Make sure the name matches with the section name in import-config.ini file",
    required=True,
)
def project_import_cmd(project_name):
    pimport = None
    config = _read_config_file(
        os.path.expanduser("~") + "/.cmlutils/import-config.ini", project_name
    )

    username = config[USERNAME_KEY]
    url = config[URL_KEY]
    apiv1_key = config[API_V1_KEY]
    local_directory = config[OUTPUT_DIR_KEY]
    ca_path = config[CA_PATH_KEY]

    local_directory = get_absolute_path(local_directory)
    ca_path = get_absolute_path(ca_path)

    log_filedir = os.path.join(local_directory, project_name, "logs")
    _configure_project_command_logging(log_filedir, project_name)
    p = ProjectImporter(
        host=url,
        username=username,
        project_name=project_name,
        api_key=apiv1_key,
        top_level_dir=local_directory,
        ca_path=ca_path,
        project_slug=project_name,
    )
    logging.info("Started importing project: %s", project_name)
    try:
        validators = initialize_import_validators(
            host=url,
            username=username,
            project_name=project_name,
            top_level_directory=local_directory,
            apiv1_key=apiv1_key,
            ca_path=ca_path,
        )
        logging.info("Begin validating for import.")
        for v in validators:
            validation_response = v.validate()
            if validation_response.validation_status == ValidationResponseStatus.FAILED:
                logging.error(
                    "Validation error for project %s: %s",
                    project_name,
                    validation_response.validation_msg,
                )
                raise RuntimeError(
                    "validation error", validation_response.validation_msg
                )
        logging.info(
            "Finished validating import validations for project %s.", project_name
        )
        project_filepath = get_project_metadata_file_path(
            top_level_dir=local_directory, project_name=project_name
        )
        project_metadata = read_json_file(project_filepath)

        uses_engine = False
        if "default_project_engine_type" in project_metadata:
            uses_engine = True
            project_metadata.pop("default_project_engine_type", None)

        project_id = p.check_project_exist(project_metadata["name"])
        if project_id == None:
            logging.info(
                "Creating project %s to migrate files and metadata.", project_name
            )
            project_id = p.create_project_v2(proj_metadata=project_metadata)
        else:
            logging.warning(
                "Project %s already exist in the target workspace. Retrying the import won't update existing project settings or artifacts. Only missing artifacts will be migrated, However the project files will be synced via rsync.",
                project_metadata.get("name", ""),
            )
        if "team_name" in project_metadata:
            username = project_metadata["team_name"]
        creator_username, project_slug = p.get_creator_username()
        pimport = ProjectImporter(
            host=url,
            username=username,
            project_name=project_name,
            api_key=apiv1_key,
            top_level_dir=local_directory,
            ca_path=ca_path,
            project_slug=project_slug,
        )
        pimport.transfer_project()
        pimport.terminate_ssh_session()

        if uses_engine:
            proj_patch_metadata = {"default_project_engine_type": "legacy_engine"}
            pimport.convert_project_to_engine_based(
                proj_patch_metadata=proj_patch_metadata
            )

        pimport.import_metadata(project_id=project_id)
    except:
        logging.error("Exception:", exc_info=1)
        if pimport:
            pimport.terminate_ssh_session()
        exit()


@click.group(name="helpers")
def project_helpers_cmd():
    """
    Sub-entrypoint for helpers command
    """


@project_helpers_cmd.command("populate_engine_runtimes_mapping")
def populate_engine_runtimes_mapping():
    project_name = "DEFAULT"
    config = _read_config_file(
        os.path.expanduser("~") + "/.cmlutils/import-config.ini", project_name
    )

    username = config[USERNAME_KEY]
    url = config[URL_KEY]
    apiv1_key = config[API_V1_KEY]
    local_directory = config[OUTPUT_DIR_KEY]
    ca_path = config[CA_PATH_KEY]

    local_directory = get_absolute_path(local_directory)
    ca_path = get_absolute_path(ca_path)

    log_filedir = os.path.join(local_directory, project_name, "logs")
    _configure_project_command_logging(log_filedir, project_name)

    p = ProjectImporter(
        host=url,
        username=username,
        project_name=project_name,
        api_key=apiv1_key,
        top_level_dir=local_directory,
        ca_path=ca_path,
        project_slug=project_name,
    )

    page_token = ""

    response = p.get_all_runtimes_v2(page_token)
    if not response:
        logging.info(
            "populate_engine_runtimes_mapping: Get Runtimes API returned empty response"
        )
        return
    runtimes = response.get("runtimes", [])
    page_token = response.get("next_page_token", "")

    while len(page_token) > 0:
        response = p.get_all_runtimes_v2(page_token)
        if not response:
            break
        runtimes = runtimes + response.get("runtimes", [])
        page_token = response.get("next_page_token", "")

    if len(runtimes) > 0:
        legacy_runtime_image_map = parse_runtimes_v2(runtimes)
    else:
        logging.error(
            "populate_engine_runtimes_mapping: No runtimes present in the get_runtimes API response"
        )
        return

    # Tries to create/overwrite the data present in <home-dir>/.cmlutils/legacy_engine_runtime_constants.json
    # Please make sure utility is having necessary permissions to write/overwrite data
    try:
        with open(
            os.path.expanduser("~")
            + "/.cmlutils/"
            + "legacy_engine_runtime_constants.json",
            "w",
        ) as legacy_engine_runtime_constants:
            dump(legacy_runtime_image_map, legacy_engine_runtime_constants)
    except:
        logging.error(
            "populate_engine_runtimes_mapping: Please make sure Write Perms are set write/overwrite data."
            "Encountered Error during write/overwrite data in ",
            os.path.expanduser("~")
            + "/.cmlutils/"
            + "legacy_engine_runtime_constants.json",
        )
