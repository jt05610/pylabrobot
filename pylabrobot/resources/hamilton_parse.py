import os
from typing import Tuple, Optional

from pylabrobot.resources import (
  CarrierSite,
  Coordinate,
  CrossSectionType,
  MFXCarrier,
  Plate,
  PlateCarrier,
  TipCarrier,
  TipRack,
  TipSpot,
  Well,
  WellBottomType,
  create_equally_spaced_2d,
  create_homogeneous_carrier_sites
)
from pylabrobot.resources.ml_star.tip_creators import (
  low_volume_tip_no_filter,
  low_volume_tip_with_filter,
  standard_volume_tip_no_filter,
  standard_volume_tip_with_filter,
  high_volume_tip_no_filter,
  high_volume_tip_with_filter,
  four_ml_tip_with_filter,
  five_ml_tip,
  five_ml_tip_with_filter
)
from pylabrobot.utils.file_parsing import find_int, find_float, find_string


__all__ = [
  "create_plate",
  "create_tip_rack",
  "create_plate_carrier",
  "create_tip_carrier",
  "create_flex_carrier",
]


def get_resource_type(filepath) -> str:
  """ Get the resource type from the filename or the file contents. """
  filename = os.path.basename(filepath)
  if filename.startswith("PLT_CAR_"):
    return "PlateCarrier"
  if filename.startswith("TIP_CAR_"):
    return "TipCarrier"
  if filename.startswith("MFX_CAR_"):
    return "MFXCarrier"
  if filename.startswith("SMP_CAR_"):
    raise ValueError("SMP_CAR_ not supported yet")
    # return "TubeCarrier"

  if filepath.endswith("_L.rck"):
    filepath = filepath.replace("_L.rck", ".rck")
  if filename.endswith("_P.rck"):
    filepath = filepath.replace("_P.rck", ".rck")

  if not os.path.exists(filepath):
    return "TipRack" # only tip racks have no .rck file

  with open(filepath, "r", encoding="ISO-8859-1") as f:
    c = f.read()
    try:
      category0id = find_int("Category.0.Id", c)
      # based on some inspection of the files, but just a guess
      if category0id in range(170, 180):
        return "TipRack"
      if category0id in range(1000, 1100):
        return "Plate"
    except ValueError:
      pass

    try:
      _ = find_string("Cntr.1.file", c) # only plates have a .ctr file
      return "Plate"
    except ValueError:
      pass

  raise ValueError(f"Unknown resource type for file {filename}")


def create_plate_for_writing(
  filepath: str,
  ctr_filepath: Optional[str] = None
) -> Tuple[Plate, Optional[str], Optional[str]]:
  """ Create a plate from the given file. Returns the plate and optionally a description. Also
  returns a description and the volume equation.

  Args:
    filepath: The path to the .rck file for the plate.
    ctr_filepath: The path to the .ctr file for the plate. If not given, it will be inferred from
      the .rck file. I think the ctr file is used for well definitions.
  """
  with open(filepath, "r", encoding="ISO-8859-1") as f:
    c = f.read()

  size_x = find_float("Dim.Dx", c)
  size_y = find_float("Dim.Dy", c)
  size_z = find_float("Dim.Dz", c)

  num_items_x = find_int("Columns", c)
  num_items_y = find_int("Rows", c)
  well_dx = find_float("Dx", c)
  well_dy = find_float("Dy", c)

  # rck files use the center of the well, but we want the bottom left corner.
  dx = round(find_float("BndryX", c) - well_dx/2, 4)
  dy = round(find_float("BndryY", c) - well_dy/2, 4)
  # dz = round(find_float("Cntr.1.base", c), 4)

  filename = os.path.basename(filepath)
  cname = filename.split(".")[0]
  description = cname

  if cname == "Cos_96_ProtCryst" and well_dy == 4.5:
    # ad-hoc fix for Cos_96_ProtCryst, where the definition is almost certainly wrong
    well_dy = 9.0

  # .rck to .ctr filepath
  def rck2ctr(fn):
    return fn \
      .replace("_P.rck", ".ctr") \
      .replace("_L.rck", ".ctr") \
      .replace(".rck", ".ctr") \
      .replace("ProtCryst", "Post")

  ctr_filepath = ctr_filepath or rck2ctr(filepath)
  with open(ctr_filepath, "r", encoding="ISO-8859-1") as f2:
    c2 = f2.read()
    num_segments = find_int("Segments", c2)
    vol_eqn_func = ""
    height_so_far = 0
    for i in range(num_segments, 0, -1):
      vol_eqn = find_string(f"{i}.EqnOfVol", c2)
      section_max_height = find_float(f"{i}.Max", c2)
      if i == num_segments: # first section from bottom
        vol_eqn = vol_eqn.replace("h", f"min(h, {section_max_height})")
        vol_eqn_func += f"volume = {vol_eqn}\n"
      else:
        vol_eqn = vol_eqn.replace("h", f"(h-{height_so_far})")
        vol_eqn_func += f"if h <= {section_max_height}:\n"
        vol_eqn_func += f"  volume += {vol_eqn}\n"
      height_so_far += section_max_height
    vol_eqn_func += f"if h > {height_so_far}:\n"
    vol_eqn_func +=  f"  raise ValueError(f\"Height {{h}} is too large for {cname}\")\n"
    vol_eqn_func += "return volume"

    well_size_x = find_float("Dim.Dx", c2)
    well_size_y = find_float("Dim.Dy", c2)

    # we can get shapes of other segments with X.Shape, X being the segment number.
    # Numbered from the top, so last segment is the bottom
    well_bottom_type_code = find_int(f"{num_segments}.Shape", c2)
    well_bottom_type = {
      0: WellBottomType.FLAT, # cylinder
      1: WellBottomType.FLAT, # rectangle
      # 2: ? # "inverted cone"
      3: WellBottomType.V,    # "V-cone"
      # 4 & 5 only for last segment
      4: WellBottomType.U,    # "rounded base segment"
      5: WellBottomType.V,    # "V-cone base segment"
    }.get(well_bottom_type_code, WellBottomType.UNKNOWN)

    # The shape of the first segment is most indicative of the well shape
    cross_section_type_code = find_int("1.Shape", c2)
    cross_section_type = {
      0: CrossSectionType.CIRCLE,
      1: CrossSectionType.RECTANGLE,
      # 2: ?? ,
      # 3: ?? ,
      # 4: ?? ,
      # 5: ?? ,
    }.get(cross_section_type_code, CrossSectionType.CIRCLE)

    well_size_z = find_float("Depth", c2)

    # probably wrong, will fix later when I do carrier site bases
    # written on 2024-03-01
    try:
      dz = find_float("BaseMM", c2)
    except ValueError:
      dz = 0

  plate = Plate(
    name=cname,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    num_items_x=num_items_x,
    num_items_y=num_items_y,
    items=create_equally_spaced_2d(
      Well,
      num_items_x=num_items_x,
      num_items_y=num_items_y,
      dx=dx + (well_dx - well_size_x)/2, # add mini offset for border of wells
      dy=dy + (well_dy - well_size_y)/2, # add mini offset for border of wells
      dz=dz,
      item_dx=well_dx,
      item_dy=well_dy,
      size_x=well_size_x,
      size_y=well_size_y,
      size_z=well_size_z,
      bottom_type=well_bottom_type,
      cross_section_type=cross_section_type
    ),
    lid_height=10,
    model=cname
  )

  return plate, description, vol_eqn_func


def create_tip_rack_for_writing(filepath: str) -> Tuple[TipRack, Optional[str]]:
  """ Create a tip rack from the given file. Returns the tip rack and optionally a description. Also
  create a description. """

  tip_table = {
    "MlStar4mlTipWithFilter": four_ml_tip_with_filter,
    "MlStar5mlTipWithFilter": five_ml_tip_with_filter,
    "MlStar10ulLowVolumeTip": low_volume_tip_no_filter,
    "MlStar10ulLowVolumeTipWithFilter": low_volume_tip_with_filter,
    "MlStar1000ulHighVolumeTipWithFilter": high_volume_tip_with_filter,
    "MlStar1000ulHighVolumeTip": high_volume_tip_no_filter,
    "MlStar5mlTip": five_ml_tip,
    "MlStar300ulStandardVolumeTipWithFilter": standard_volume_tip_with_filter,
    "MlStar300ulStandardVolumeTip": standard_volume_tip_no_filter,
  }

  with open(filepath, "r", encoding="ISO-8859-1") as f:
    c = f.read()

  size_x = find_float("Dim.Dx", c)
  size_y = find_float("Dim.Dy", c)
  size_z = find_float("Dim.Dz", c)
  tip_type = None
  try:
    tip_type = find_string("PropertyValue.6", c)
  except ValueError:
    tip_type = find_string("PropertyValue.4", c)
  tip_creator = tip_table[tip_type]

  tip_size_x = find_float("Dx", c)
  tip_size_y = find_float("Dy", c)

  # rck files use the center of the well, but we want the bottom left corner.
  dx = round(find_float("BndryX", c) - tip_size_x/2, 4)
  dy = round(find_float("BndryY", c) - tip_size_y/2, 4)
  dz = find_float("Cntr.1.base", c)

  num_items_x = find_int("Columns", c)
  num_items_y = find_int("Rows", c)

  cname = os.path.basename(filepath).split(".")[0]
  if cname[0] == "4":
    cname = "Four" + cname[1:]
  elif cname[0] == "5":
    cname = "Five" + cname[1:]
  description = find_string("Description", c)

  tip_rack = TipRack(
    name=cname,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    items=create_equally_spaced_2d(
      TipSpot,
      num_items_x=num_items_x,
      num_items_y=num_items_y,
      dx=dx,
      dy=dy,
      dz=dz,
      item_dx=tip_size_x,
      item_dy=tip_size_y,
      size_x=tip_size_x,
      size_y=tip_size_y,
      size_z=size_z,
      make_tip=tip_creator
    ),
    model=cname
  )

  return tip_rack, description


def create_plate_carrier_for_writing(filepath: str) -> Tuple[PlateCarrier, Optional[str]]:
  """ Create a plate carrier from the given file. Returns the plate carrier and optionally a
  description. Also create a description. """
  with open(filepath, "r", encoding="ISO-8859-1") as f:
    c = f.read()

  site_count = int(c.split("Site.Cnt\x01")[1].split("\x08")[0])
  sites = []
  for i in range(1, site_count+1):
    x = find_float(f"Site.{i}.X", c)
    y = find_float(f"Site.{i}.Y", c)
    z = find_float(f"Site.{i}.Z", c)
    site_width = find_float(f"Site.{i}.Dx", c)
    site_height = find_float(f"Site.{i}.Dy", c)
    sites.append(Coordinate(x, y, z))
  sites = sorted(sites, key=lambda c: c.y)

  size_x = find_float("Dim.Dx", c)
  size_y = find_float("Dim.Dy", c)
  size_z = find_float("Dim.Dz", c)
  description = find_string("Description", c)
  cname = os.path.basename(filepath).split(".")[0]

  plate_carrier = PlateCarrier(
    name=cname,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    sites=create_homogeneous_carrier_sites(klass=CarrierSite, locations=sites,
                                           site_size_x=site_width, site_size_y=site_height),
    model=cname
  )
  return plate_carrier, description


def create_tip_carrier_for_writing(filepath: str) -> Tuple[TipCarrier, Optional[str]]:
  """ Create a tip carrier from the given file. Returns the tip carrier and optionally a
  description. Also create a description. """
  with open(filepath, "r", encoding="ISO-8859-1") as f:
    c = f.read()

  site_count = int(c.split("Site.Cnt\x01")[1].split("\x08")[0])
  sites = []
  for i in range(1, site_count+1):
    x = find_float(f"Site.{i}.X", c)
    y = find_float(f"Site.{i}.Y", c)
    z = find_float(f"Site.{i}.Z", c)
    site_width = find_float(f"Site.{i}.Dx", c)
    site_height = find_float(f"Site.{i}.Dy", c)
    sites.append(Coordinate(x, y, z))
  sites = sorted(sites, key=lambda c: c.y)

  size_x = find_float("Dim.Dx", c)
  size_y = find_float("Dim.Dy", c)
  size_z = find_float("Dim.Dz", c)
  description = find_string("Description", c)
  cname = os.path.basename(filepath).split(".")[0]

  tip_carrier = TipCarrier(
    name=cname,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    sites=create_homogeneous_carrier_sites(klass=CarrierSite, locations=sites,
                                           site_size_x=site_width, site_size_y=site_height),
    model=cname
  )
  return tip_carrier, description


def create_flex_carrier_for_writing(filepath: str) -> Tuple[MFXCarrier, Optional[str]]:
  """ Create a multiflex carrier from the given file. Returns the multiflex carrier and optionally a
  description. Also create a description. """
  with open(filepath, "r", encoding="ISO-8859-1") as f:
    c = f.read()

  site_count = int(c.split("Site.Cnt\x02")[1].split("\x08")[0])
  sites = []
  for i in range(1, site_count+1):
    x = find_float(f"Site.{i}.X", c)
    y = find_float(f"Site.{i}.Y", c)
    z = find_float(f"Site.{i}.Z", c)
    site_width = find_float(f"Site.{i}.Dx", c)
    site_height = find_float(f"Site.{i}.Dy", c)
    sites.append(Coordinate(x, y, z))
  sites = sorted(sites, key=lambda c: c.y)

  # filter sites by visible
  sites = [s for i, s in enumerate(sites) if find_int(f"Site.{i}.Visible", c) == 1]

  size_x = find_float("Dim.Dx", c)
  size_y = find_float("Dim.Dy", c)
  size_z = find_float("Dim.Dz", c)
  description = find_string("Description", c)
  cname = os.path.basename(filepath).split(".")[0]

  flex_carrier = MFXCarrier(
    name=cname,
    size_x=size_x,
    size_y=size_y,
    size_z=size_z,
    sites=create_homogeneous_carrier_sites(klass=CarrierSite, locations=sites,
                                           site_size_x=site_width, site_size_y=site_height),
    model=cname
  )
  return flex_carrier, description


def create_plate(filepath: str, name: str, ctr_filepath: Optional[str] = None) -> Plate:
  """ Create a plate from the given file.

  Args:
    filepath: The path to the .rck file for the plate.
    name: The name of the plate resource.
  """
  plate, _, _ = create_plate_for_writing(filepath, ctr_filepath=ctr_filepath)
  plate.name = name
  return plate


def create_tip_rack(filepath: str, name: str) -> TipRack:
  """ Create a tip rack from the given file.

  Args:
    filepath: The path to the .rck file for the tip rack.
    name: The name of the tip rack resource.
  """
  tip_rack, _ = create_tip_rack_for_writing(filepath)
  tip_rack.name = name
  return tip_rack


def create_plate_carrier(filepath: str, name: str) -> PlateCarrier:
  """ Create a plate carrier from the given file.

  Args:
    filepath: The path to the .rck file for the plate carrier.
    name: The name of the plate carrier resource.
  """
  plate_carrier, _ = create_plate_carrier_for_writing(filepath)
  plate_carrier.name = name
  return plate_carrier


def create_tip_carrier(filepath: str, name: str) -> TipCarrier:
  """ Create a tip carrier from the given file.

  Args:
    filepath: The path to the .rck file for the tip carrier.
    name: The name of the tip carrier resource.
  """
  tip_carrier, _ = create_tip_carrier_for_writing(filepath)
  tip_carrier.name = name
  return tip_carrier


def create_flex_carrier(filepath: str, name: str) -> MFXCarrier:
  """ Create a multiflex carrier from the given file.

  Args:
    filepath: The path to the .rck file for the multiflex carrier.
    name: The name of the multiflex carrier resource.
  """
  flex_carrier, _ = create_flex_carrier_for_writing(filepath)
  flex_carrier.name = name
  return flex_carrier
