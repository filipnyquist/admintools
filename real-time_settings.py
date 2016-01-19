#!/usr/bin/env python3

"""
This script is intended for desktop Linux, to temporarily configure
them for real-time applications (such as audio applications).
"""

import argparse
import logging
from sys import argv
from abc import ABCMeta
from os import getuid, uname as get_uname, path
from shutil import which, copyfile
from subprocess import call, check_output
from multiprocessing import cpu_count
from tempfile import gettempdir
from json import load as json_load, dump as json_dump

if __name__ != '__main__':
  raise NotImplementedError(
    "This is module should be called directly from command line."
  )

parser = argparse.ArgumentParser(
  description="Configures your Linux for real-time applications " +
              "(tested for recent Debian and Fedora systems)."
)

parser.add_argument('-d', '--debug', action='store_true', default=False,
                    help='turn on debug messages')
parser.add_argument('-v', '--verbose', action='store_true', default=False,
                    help='turn on verbose messages')
parser.add_argument('-s', '--simulate', action='store_true',
                    default=False,
                    help='simulate and print what would be executed')
parser.add_argument('-l', '--list', action='store_true',
                    default=False,
                    help='list available settings modules and exit')
parser.add_argument('-i', '--ignore', nargs='*', default=[],
                    help='list of settings modules to ignore')
parser.add_argument('on_or_off', choices = ['on', 'off'], nargs='?',
                    help='Activate or rt_settings_off.')

cli_args = parser.parse_args()

# set up logger
logging.getLogger().name = ""
if cli_args.debug:
  logging.getLogger().setLevel(logging.DEBUG)
if cli_args.verbose:
  logging.getLogger().setLevel(logging.INFO)

settings_modules = []

#
# settings module definitions
#

class ActionBase(metaclass=ABCMeta):

  TEMPFILE = path.join(gettempdir(), "real-time_settings")

  def rt_settings_on(self):
    pass

  def rt_settings_off(self):
    pass

  def execute_safely(self, function, *args, **kwargs):
    """
    Method prints what would be done if simulating or
    does it otherwise.
    """
    def as_pretty_string():
      return "%s.%s(%s, %s)" % (
        function.__module__,
        function.__name__,
        ', '.join((repr(arg) for arg in args)),
        ', '.join(( "%s=%s" % (repr(k), repr(v))
              for k, v in kwargs.items())),
      )

    if cli_args.simulate:
      print("simulating - would execute: %s" % (
        as_pretty_string()
      ))
      return
    else:
      logging.debug("executing " + as_pretty_string())
      return function(*args, **kwargs)

  def service(self, name, action):

    if not hasattr(ActionBase, "_available_services_cache"):
      ActionBase._available_services_cache = []
      output = check_output(("systemctl", "list-unit-files"),
                            universal_newlines=True)
      for line in output.split("\n")[1:-3]:
        service_name = path.splitext(line.split(" ")[0])[0]
        if service_name != "":
          ActionBase._available_services_cache.append(service_name)
      logging.debug("found services %r" %
                    ActionBase._available_services_cache)

    if name not in ActionBase._available_services_cache:
      logging.info("service '%s' does not seem to exist" % name)
      return

    self.execute_safely(call, (which("systemctl"), action, name))

  def service_start(self, name):
    self.service(name, "start")

  def service_stop(self, name):
    self.service(name, "stop")

  def _load_all_from_temp(self):
    try:
      with open(self.TEMPFILE, "r") as handle:
          return json_load(handle)
    except (ValueError, FileNotFoundError):
      return {}

  def load_from_temp(self, key, default=None):
    self._load_all_from_temp().get(key, default)

  def store_to_temp(self, key, value):
    obj = self._load_all_from_temp()
    obj[key] = value
    with open(self.TEMPFILE, "w+") as handle:
      return json_dump(obj, handle)

class CheckForRealTimeKernel(ActionBase):
  def rt_settings_on(self):
    SYS_RT_FILE = "/sys/kernel/realtime"
    if path.isfile(SYS_RT_FILE):
      with open(SYS_RT_FILE) as sys_rt:
        if sys_rt.readline() == "1":
          logging.debug("found %s with '1' in it" % SYS_RT_FILE)
          return
    uname = get_uname()
    if uname.release.endswith("+rt"):
      logging.debug("found +rt in kernel release")
      return
    if " RT " in uname.version:
      logging.debug("found RT in kernel version")
      return
    if " PREEMPT " in uname.version:
      logging.debug("found PREEMT in kernel version")
      return

    logging.warn("You don't seem to be using a real-time kernel.")
settings_modules.append(CheckForRealTimeKernel)

class Cron(ActionBase):
  def rt_settings_on(self):
    self.service_stop("cron")
    self.service_stop("crond")
  def rt_settings_off(self):
    self.service_start("cron")
    self.service_start("crond")
settings_modules.append(Cron)

class Tlp(ActionBase):
  def rt_settings_on(self):
    self.service_stop("tlp")
  def rt_settings_off(self):
    self.service_start("tlp")
settings_modules.append(Tlp)

class FrequencyScaling(ActionBase):
  CPUFREQ_BASE_PATH = "/sys/devices/system/cpu/cpu%i/cpufreq"
  CPUFREQ_MIN = path.join(CPUFREQ_BASE_PATH, "cpuinfo_min_freq")
  CPUFREQ_MAX = path.join(CPUFREQ_BASE_PATH, "cpuinfo_max_freq")
  CPUFREQ_MIN_ALLOWED = path.join(CPUFREQ_BASE_PATH, "scaling_min_freq")
  HAS_PSTATES = path.exists("/sys/devices/system/cpu/intel_pstate")
  def rt_settings_on(self):
    for cpu_num in range(cpu_count()):
      self.execute_safely(
        copyfile,
        self.CPUFREQ_MAX % cpu_num,
        self.CPUFREQ_MIN_ALLOWED % cpu_num
      )
  def rt_settings_off(self):
    for cpu_num in range(cpu_count()):
      self.execute_safely(
        copyfile,
        self.CPUFREQ_MIN % cpu_num,
        self.CPUFREQ_MIN_ALLOWED % cpu_num
      )
settings_modules.append(FrequencyScaling)

class IntelPState(ActionBase):
  PSTATE_NO_TURBO_FILE_NAME = "/sys/devices/system/cpu/intel_pstate/no_turbo"
  PSTATE_NO_TURBO_ON = 0
  PSTATE_NO_TURBO_OFF_DEFAULT = 1

  def _store_current_and_replace(self, file_name, value):
    file_path = path.join(self.PSTATE_BASE_PATH, file_name)
    with open(file_path) as handle:
      content = handle.readline().strip()
    self.store_to_temp(file_path, content)
    with open(file_path, "w+") as handle:
      handle.write(str(value))

  def _restore_or_set(self, file_name, default):
    file_path = path.join(self.PSTATE_BASE_PATH, file_name)
    loaded = self.load_from_temp(file_name)
    with open(file_path, "w+") as handle:
      handle.write(str(loaded or default))

  def rt_settings_on(self):
    self.execute_safely(self._store_current_and_replace,
                        self.PSTATE_NO_TURBO_FILE_NAME,
                        self.PSTATE_NO_TURBO_ON)
  def rt_settings_off(self):
    self.execute_safely(self._restore_or_set,
                        self.PSTATE_NO_TURBO_FILE_NAME,
                        self.PSTATE_NO_TURBO_OFF_DEFAULT)
settings_modules.append(IntelPState)


#
# execution of settings modules
#

if cli_args.list:
  for action in settings_modules:
    print(action.__name__)
  exit(0)

# assort ignored settings module
unignored_settings_modules = list(settings_modules)
for settings_module in settings_modules:
  settings_module_name = settings_module.__name__
  if settings_module_name in cli_args.ignore:
    unignored_settings_modules.remove(settings_module)
    cli_args.ignore.remove(settings_module_name)
if cli_args.ignore:
  logging.error(
    "Could not find settings modules to ignore: %r" % cli_args.ignore
  )
  exit(1)
settings_modules = unignored_settings_modules

if cli_args.on_or_off and getuid():
  logging.warn("You'll probably have to run this as root (sudo) "
               "but I'll continue and try.")

if cli_args.on_or_off == "on":
  for SettingsModule in settings_modules:
    logging.info("real-time settings on: %s" % SettingsModule.__name__)
    SettingsModule().rt_settings_on()
elif cli_args.on_or_off == "off":
  for SettingsModule in settings_modules:
    logging.info("real-time settings off: %s" % SettingsModule.__name__)
    SettingsModule().rt_settings_off()
else:
  print("please provide either 'on' or 'off' as last argument (see -h)")
