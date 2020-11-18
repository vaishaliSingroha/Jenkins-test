"""
Copyright 2020 The Magma Authors.

This source code is licensed under the BSD-style license found in the
LICENSE file in the root directory of this source tree.

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import collections
import distutils.util
import json
import os
import re
import time

from fabric.api import cd, env, hide, local, run, settings
from fabric.operations import put, sudo
from fabric.utils import abort, fastprint


CONFIG_FILE = "fabfile_teravm_conf.json"

with open(CONFIG_FILE) as config_file:
    config = json.load(config_file)

VM_IP_MAP = config["setups"]
NG40_TEST_FILES = config["general"]["ng40_test_files"]
DEFAULT_KEY_FILENAME = config["general"]["key_filename"]
FEG_DOCKER_COMPOSE_GIT = config["general"]["feg_docker_compose_git"]
AGW_ATP_PUBKEY = config["general"]["agw_apt_etagecom_pubkey"]
AGW_APT_SOURCE = config["general"]["agw_apt_etagecom_source"]
AGW_APT_BRANCH = config["general"]["agw_apt_etagecom_branch"]
AGW_ATP_FILE = config["general"]["agw_apt_source_file"]

fastprint("Configuration loaded\n")


# Both authorized key based ssh and cert-based ssh are setup from magma-driver
# to ag, feg, controller,and proxy in teravm so no need to provide password
# for ssh commands.Looks like fab env.key_filename only works with authorized
# key based ssh. A bash script can take advantage of cert-based ssh.


def upgrade_and_run_3gpp_tests(
    setup,
    key_filename=DEFAULT_KEY_FILENAME,
    hash=None,
    custom_test_file=NG40_TEST_FILES,
    upgrade_cloud="True",
    upgrade_feg="True",
    upgrade_agw="True",
):
    """
    Runs upgrade and s6a and gxgy tests once. This is run in the cron job on
    magma-driver:
    fab upgrade_and_run_3gpp_tests: 2>&1 | tee /tmp/teravm_cronjob.log

    key_filename: path to where the private key is for authorized-key based
    ssh. The public key counterpart needs to in the authorized_keys file on
    the remote host. If empty file name is passed, password-based ssh will
    work instead. This can be used if the script is run manually.

    custom_test_file: a 3gpp test file to run. The default uses s6a and gxgy
    """
    upgrade_teravm(
        setup,
        key_filename=key_filename,
        hash=hash,
        upgrade_cloud=upgrade_cloud,
        upgrade_feg=upgrade_feg,
        upgrade_agw=upgrade_agw,
    )
    fastprint("Sleeping for 30 seconds to make sure system is ready\n")
    time.sleep(30)

    verdicts = run_3gpp_tests(setup, key_filename, custom_test_file)
    return verdicts

def upgrade_teravm(
    setup,
    hash=None,
    key_filename=DEFAULT_KEY_FILENAME,
    upgrade_feg="True",
    upgrade_agw="True",
):
    """
    Upgrade teravm vms feg, agw.
    This will be run by a cron job on magma-driver(192.168.60.109) in teraVM.
    magma-driver is the control vm in teraVM. It will run a cron job that
    upgrades and runs teraVM tests automatically.

    Alternatively, this script can be run from a local machine that is on TIP
    lab VPN to 192.168.60.0/24. When run manually, a hash can be provided to
    specify what are the hash of the images that it should pull and use to
    upgrade test vms.

    hash: a hash to identify what images to pull from s3 bucket and use
    for upgrading. If None, try find the most recent hash.

    key_filename: path to where the private key is for authorized-key based
    ssh. The public key counterpart needs to in the authorized_keys file on
    the remote host. If empty file name is passed, password-based ssh will
    work instead. This can be used if the script is run manually.
    """
    upgrade_feg = _prep_bool_arg(upgrade_feg)
    upgrade_agw = _prep_bool_arg(upgrade_agw)

    fastprint("Hash to use: %s\n" % hash)

    if upgrade_agw:
        upgrade_teravm_agw(setup, hash, key_filename)

    if upgrade_feg:
        upgrade_teravm_feg(setup, hash, key_filename)

    return hash

def get_agw(setup):
    version = None
    """
    Upgrade teravm agw to image with the given hash.
    hash: a hash to identify what version from APT to use for upgrading.
    If not hash provided or "latest" is passed, it will install latest
    on the repository.

    key_filename: path to where the private key is for authorized-key based
    ssh. The public key counterpart needs to in the authorized_keys file on
    the remote host. If empty file name is passed, password-based ssh will
    work instead. This can be used if the script is run manually.
    """

    fastprint("Upgrade teraVM AGW to %s")
    _setup_env_agw("magma", VM_IP_MAP[setup]["gateway"])
    err = _set_magma_apt_repo()
    if err:
        return err
    sudo("apt update")
    fastprint("Get latest version\n")
    # Get the whole version string containing that hash and 'apt install' it
    with settings(abort_exception=FabricException):
	version = sudo ("apt-cache madison magma | awk 'NR==1{{print substr ($3,1)}}'")
        #fastprint(version)
    return version

def upgrade_teravm_agw(setup, key_filename=DEFAULT_KEY_FILENAME):
    """
    Upgrade teravm agw to image with the given hash.
    hash: a hash to identify what version from APT to use for upgrading.
    If not hash provided or "latest" is passed, it will install latest
    on the repository.

    key_filename: path to where the private key is for authorized-key based
    ssh. The public key counterpart needs to in the authorized_keys file on
    the remote host. If empty file name is passed, password-based ssh will
    work instead. This can be used if the script is run manually.
    """

    fastprint("Upgrade teraVM AGW to %s")
    _setup_env("magma", VM_IP_MAP[setup]["gateway"], key_filename)
    err = _set_magma_apt_repo()
    if err:
        return err
    sudo("apt update")
    fastprint("Install version with latest version\n")
    # Get the whole version string containing that hash and 'apt install' it
    with settings(abort_exception=FabricException):
        try:
            version = sudo ("apt-cache madison magma | awk 'NR==1{{print $3}}'")
            sudo(
            "version=$("
            "apt-cache madison magma | awk 'NR==1{{print $3}}');"
            "apt install magma=$version -V".format()
            )
            status = ("Upgrade Status : Successful AGW upgrade : %s \n" % version)
            fastprint(status)
        except Exception:
            status = (
                "Upgrade Status : Error during install of version {%s} on AGW.: "
                "Maybe the version doesn't exist. Not installing. \n" % version
            )
            fastprint(status)
    return status


def upgrade_teravm_agw_AWS(setup, hash, key_filename=DEFAULT_KEY_FILENAME):
    """
    Upgrade teravm agw to image with the given hash.
    hash: a hash to identify what images to pull from s3 bucket and use
    for upgrading. If None, try find the most recent hash.

    key_filename: path to where the private key is for authorized-key based
    ssh. The public key counterpart needs to in the authorized_keys file on
    the remote host. If empty file name is passed, password-based ssh will
    work instead. This can be used if the script is run manually.
    """
    fastprint("Upgrade teraVM AGW through AWSto %s" % hash)
    _setup_env("magma", VM_IP_MAP[setup]["gateway"], key_filename)
    try:
        image = _get_gateway_image(hash)
    except Exception:
        fastprint("Image %s not found. Not updating AGW \n" % hash)
        return
    _fetch_image("ag", "gateway/%s" % image)

    with cd("/tmp/images"):
        run("tar -xzf %s" % image)
        # --fix-broken to avoid the case where a previous manual
        # install didn't leave missing libraries.
        sudo(
            "apt --fix-broken -y install -o "
            'Dpkg::Options::="--force-confnew" --assume-yes --force-yes'
        )
        sudo("apt-get update -y")
        sudo("apt-get autoremove -y")
        sudo(
            "apt --fix-broken -y install -o "
            'Dpkg::Options::="--force-confnew" --assume-yes --force-yes'
        )
        sudo("dpkg --force-confnew -i magma*.deb")
        sudo("apt-get install -f -y")
        sudo("systemctl stop magma@*")
        sudo("systemctl restart magma@magmad")


def upgrade_teravm_feg(setup, key_filename=DEFAULT_KEY_FILENAME):
    """
    Upgrade teravm feg to the image with the given hash.

    hash: a hash to identify what images to pull from s3 bucket and use
    for upgrading. If None, try find the most recent hash.

    key_filename: path to where the private key is for authorized-key based
    ssh. The public key counterpart needs to in the authorized_keys file on
    the remote host. IIf empty file name is passed, password-based ssh will
    work instead. This can be used if the script is run manually.
    """
    err = None
    version = get_agw(setup)
    fastprint("Upgrade teraVM FEG to %s\n" % version)
    _setup_env("magma", VM_IP_MAP[setup]["feg"], key_filename)
    with cd("/var/opt/magma/docker"), settings(abort_exception=FabricException):
        sudo("docker-compose down")
        sudo("cp docker-compose.yml docker-compose.yml.backup")
        sudo("cp .env .env.backup")
        version1 = version.split("-")[2]
        #sudo('sed -i "s/IMAGE_VERSION=.*/IMAGE_VERSION=%s/g" .env' version )
        sudo('sed -i "s/^IMAGE_VERSION=.*$/IMAGE_VERSION=%s/g" .env' % version1)
        if len(_check_disk_space()) != 0:
            fastprint("Disk space alert: cleaning docker images\n")
            sudo("docker system prune --all  --force")
        try:
            # TODO: obtain .yml file from jfrog artifact instead of git master
            sudo("wget -O docker-compose.yml %s" % FEG_DOCKER_COMPOSE_GIT)
            sudo("docker-compose up -d")
            status = ("\nUpgrade Status : Successful FEG upgrade : %s \n" % version1)
            fastprint(status)
        except Exception:
            status = (
                "Upgrade Status : Error during install of version {} on FEG. Maybe the image "
                "doesn't exist:. Reverting to the original "
                "config \n".format(version1)
            )
            fastprint(status)
            with hide("running", "stdout"):
                sudo("mv docker-compose.yml.backup docker-compose.yml")
                sudo("mv .env.backup .env")
                sudo("docker-compose up -d")
    return status


def run_3gpp_tests(
    setup, key_filename=DEFAULT_KEY_FILENAME, test_files=NG40_TEST_FILES
):
    """
    Run teravm s6a and gxgy test cases. Usage: 'fab run_3gpp_tests:' for
    default key filename and default test files.

    key_filename: path to where the private key is for authorized-key based
    ssh. The public key counterpart needs to in the authorized_keys file on
    the remote host. If empty file name is passed, password-based ssh will
    work instead. This can be used if the script is run manually.

    test_file: a test file to use instead of the s6a and gxgy defaults.
    """
    if isinstance(test_files, str):
        test_files = [test_files]
    test_output = []

    for test_file in test_files:
        fastprint("Run test for file %s\n" % (test_file))
        _setup_env("ng40", VM_IP_MAP[setup]["ng40"], key_filename)
        with cd("/home/ng40/magma/automation"):
            with hide("warnings", "running", "stdout"), settings(warn_only=True):
                output = run("ng40test %s" % test_file)
                test_output.append(output)
        fastprint("Done with file %s\n" % (test_file))
    
    verdicts = _parse_stats(test_output)
    print(verdicts)
    return verdicts


def _set_magma_apt_repo():
    err = None
    with settings(abort_exception=FabricException):
        try:
            # add repo to source file (same as add-apt-repo
            repo_apt_string = "deb {} {}".format(AGW_APT_SOURCE, AGW_APT_BRANCH)
            ignore_comments = "/^[[:space:]]*#/!"
            sudo("touch {}".format(AGW_ATP_FILE))
            # Replace non commented lines with the wrong repo, or add it if missing
            sudo(
                "grep -q '{source}' {sFile} && "
                "sed -i '{ign_com}s,.*{source}.*,{repo},g' {sFile} || "
                "echo '{repo}' >> {sFile} ".format(
                    ign_com=ignore_comments,
                    source=AGW_APT_SOURCE,
                    repo=repo_apt_string,
                    sFile=AGW_ATP_FILE,
                )
            )
        except Exception:
            err = "Error changing ATP repo\n"
            fastprint(err)
    return err


def _parse_stats(teravm_raw_result):
    """
    Gets stats from teraVM result output string

    teravm_test_result: output comming from the teravm stdout
    """
    verdicts = collections.defaultdict(list)

    pattern = r"Verdict\((?P<test_case>\w+)\) = VERDICT_(?P<verdict>\w+)"
    for fileResults in teravm_raw_result:
        for line in fileResults.splitlines():
            match = re.match(pattern, line)
            if match:
                verdict = match.groupdict()["verdict"]
                verdicts[verdict].append(line)
    return verdicts


def _check_disk_space(threshold=80, drive_prefix="/dev/sd"):
    over_threshold = {}
    with hide("running", "stdout", "stderr"), settings(warn_only=True):
        columns = sudo("df -hP | awk 'NR>1{print $1,$5}' | sed -e's/%//g'")

        for line in columns.split("\n"):
            line = line.split(" ")
            if len(line) != 2:
                continue
            dev = line[0]
            dev.strip()
            percentage = int(line[1])
            if dev.startswith(drive_prefix) and percentage >= threshold:
                over_threshold[dev] = percentage

    return over_threshold


def _get_gateway_image(hash):
    output = local(
        "aws s3 ls s3://magma-images/gateway/ "
        "| grep %s.deps.tar.gz | sort -r | head -1" % hash,
        capture=True,
    )
    if len(output) == 0:
        raise Exception("No gateway image found with hash %s" % hash)
    else:
        return output.rsplit(" ", 1)[1]


def _fetch_image(name, image):
    """
    Fetches the image from s3 and copies the image to /tmp/images in the VM
    """
    # Make local directory
    local("rm -rf /tmp/%s-images" % name)
    local("mkdir -p /tmp/%s-images" % name)
    # Fetch image from s3
    local("aws s3 cp 's3://magma-images/%s' /tmp/%s-images" % (image, name))
    # create /tmp/images directory on remote host
    # env has to be set up before calling this function
    _setup_env("magma", VM_IP_MAP["setup_1"]["gateway"], DEFAULT_KEY_FILENAME)
    run("rm -rf /tmp/images")
    run("mkdir -p /tmp/images")
    # copy images from local /tmp to corresponding remote /tmp/images
    put("/tmp/%s-images/*" % name, "/tmp/images/")


def _setup_env(username, remote_machine_ip, key_filename):
    env.key_filename = [key_filename]
    env.host_string = "%s@%s" % (username, remote_machine_ip)
    env.user = username

def _setup_env_agw(username, remote_machine_ip):
    env.host_string = "%s@%s" % (username, remote_machine_ip)
    env.user = username

def _prep_bool_arg(arg):
    return bool(distutils.util.strtobool(str(arg)))


class FabricException(Exception):
    pass

