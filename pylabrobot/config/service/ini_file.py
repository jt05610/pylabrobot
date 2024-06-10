import configparser
from pathlib import Path
from typing import IO

from pylabrobot.config.config import Config
from pylabrobot.config.service import ConfigReader
from pylabrobot.config.service import ConfigWriter


class IniReader(ConfigReader):
  """A ConfigReader that reads from an IO stream that INI formatted."""

  extension = "ini"

  def read(self, r: IO) -> Config:
    """Read a Config object from an opened IO stream that is INI formatted."""
    config = configparser.ConfigParser()
    config.read_file(r)
    log_config = config["logging"]
    return Config(logging=Config.Logging(log_dir=Path(log_config["log_dir"])))


class IniWriter(ConfigWriter):
  """A ConfigWriter that writes to an IO stream in INI format."""

  extension = "ini"

  def write(self, w: IO, cfg: Config):
    """Write a Config object to an IO stream in INI format."""
    config = configparser.ConfigParser()
    for k, v in cfg.as_dict.items():
      config[k] = v

    config.write(w)
    return w
