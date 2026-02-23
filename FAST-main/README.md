# ATTENTION: The latest version of this tool is available at https://msc.fema.gov/portal/resources/hazus This github repository is no longer receiving updates.

# Hazus Flood Assessment Structure Tool

The Hazus Flood Assessment Structure Tool (FAST) calculates building-level flood impacts with user-provided building and flood depth data. FAST uses the Hazus Flood model methodology to assign depth damage functions to buildings according to their occupancy type, first floor elevation, foundation type, and number of stories. Flood depth is then extracted at every building and used as a depth damage function parameter to calculate flood losses in dollars. Flood-generated debris is estimated using building area in square feet. For more information about how FAST cacluates flood impacts, please refer to the Hazus Flood Technical Manual: https://www.fema.gov/media-library-data/20130726-1820-25045-8292/hzmh2_1_fl_tm.pdf

Building data can be supplied as `.csv` or `.parquet` according to the specifications outlined here: https://github.com/nhrap-hazus/FAST/blob/master/Help/FASTBuildingData.pdf Flood depth data must be formatted as a `.tiff` raster. Sample building data for Honolulu, HI are included in the "UDF" folder.

FAST is developed using the Hazus Python Package, HazPy. HazPy tools automatically check for updates each time they are opened. Hazus Python Package documentation is found here: https://github.com/nhrap-hazus/hazus. The Hazus Team would like to thank the Oregon Department of Geology and Mineral Industries for developing an early version of this tool: https://www.oregongeology.org/pubs/ofr/O-18-04/O-18-04_user_guide.pdf

## Local Extension: Parquet Input Support

This repository includes a local extension to support building inventory input from **Parquet** in addition to CSV.

- Input file types in GUI: `.csv` and `.parquet`
- Flood depth input: unchanged (`.tif/.tiff/.nc`)
- Output files: unchanged (`.csv` and sorted `_sorted.csv`)

Implementation notes:

- GUI field mapping reads Parquet schema directly to populate column candidates.
- CSV and Parquet now run through the same local Python engine in `Python_env/hazus_notinuse.py` to keep output behavior aligned.
- A headless server entrypoint is available at `Python_env/run_fast.py` (`run_fast(...)` API + CLI).
- `pyarrow` is required for Parquet mode and is added to `src/environment.yaml`.

Current limitations:

- Parquet support currently expects a single local Parquet file as input.
- Behavior and output format are intended to stay aligned with existing FAST CSV workflows.

## Server / Headless Usage

Run FAST without GUI from Python or CLI. This mode supports both CSV and Parquet inventories and uses the same engine for both formats.

### CLI example

```bash
python Python_env/run_fast.py \
  --inventory UDF/ND_Minot_UDF.csv \
  --mapping-json '{"UserDefinedFltyId":"FltyId","OCC":"Occ","Cost":"Cost","Area":"Area","NumStories":"NumStories","FoundationType":"FoundationType","FirstFloorHt":"FirstFloorHt","ContentCost":"ContentCost","BDDF_ID":"","CDDF_ID":"","IDDF_ID":"","InvCost":"","SOID":"","Latitude":"Latitude","Longitude":"Longitude"}' \
  --flc Riverine \
  --rasters BERYL_2024_adv41_e10_ResultMaskRaster.tif \
  --output-dir UDF
```

`--mapping-json` can also be a file path to a JSON object.

### Python API example

```python
from run_fast import run_fast

mapping = {
    "UserDefinedFltyId": "FltyId",
    "OCC": "Occ",
    "Cost": "Cost",
    "Area": "Area",
    "NumStories": "NumStories",
    "FoundationType": "FoundationType",
    "FirstFloorHt": "FirstFloorHt",
    "ContentCost": "ContentCost",
    "BDDF_ID": "",
    "CDDF_ID": "",
    "IDDF_ID": "",
    "InvCost": "",
    "SOID": "",
    "Latitude": "Latitude",
    "Longitude": "Longitude",
}

ok, message = run_fast(
    inventory_path="UDF/ND_Minot_UDF.parquet",
    mapping=mapping,
    flc="Riverine",
    rasters=["BERYL_2024_adv41_e10_ResultMaskRaster.tif"],
    output_dir="UDF",
    project_root=".",
)
print(ok, message)
```

### Linux/server notes

- FAST lookup tables directory is `Lookuptables` (case-sensitive on Linux).
- The engine auto-detects `Lookuptables` and writes logs to `Log/app.log` by default.
- Required runtime packages include `pyarrow`, `gdal`, and `utm`.

## Requirements

The Flood Assessment Structure Tool requires Anaconda to be installed on your computer. Anaconda is a free software that automatically manages all Python packages required to run Hazus open source tools - including the Hazus Python package: https://fema-ftp-snapshot.s3.amazonaws.com/Hazus/hazpy/build/html/index.html **Please note that FAST is not compatible with the Export Tool at this time. If you have the Export Tool installed on your machine, please remove it before downloading and installing FAST**

1. Go to https://www.anaconda.com/distribution/

2. Download Anaconda for Python 3

3. Complete the installation. During installation, make sure the following options are checked:

   - [x] Add Anaconda to my PATH environment variable
   - [x] Register Anaconda as my default Python
   - [x] Install Anaconda for local user, rather than all users
   
FAST will automatically check for HazPy updates each time it is opened. If you experience errors during this process, please try uninstalling Anaconda and reinstalling the latest version.
 
## Documentation

Please see the Help folder for building data guidance, FAST case study information (coming soon), and a brief video demonstration of FAST: https://github.com/nhrap-hazus/FAST/tree/master/Help

## Contact

Check out the [Troubleshooting](#troubleshooting) section below for help operating FAST.

Issues can be reported through the repository on Github: https://github.com/nhrap-dev/FAST/issues

For questions contact the Hazus Team at fema-hazus-support@fema.dhs.gov.

## To Use

Follow the steps below to run FAST. To ensure .py files run when double-clicked, right-click the .py file and go to Properties. Under the "General" tab next to "Opens With", make sure "python.exe" is selected. If not, click "Change" and select "python.exe" from your Python installation directory.

**1. Download zip folder from GitHub, unzip.**

![Download FAST](Images/Step1.png "Download FAST")

**2. Place your formatted building data in the "UDF" subfolder. Place your flood depth data in the "Rasters" subfolder.
Guidance for formatting building data can be found here: https://github.com/nhrap-hazus/FAST/blob/master/Help/FASTBuildingData.pdf***

![Store input data](Images/Step2.png "Store input data")

**3. Double-click "FAST.py" If you don't have the Hazus Python Library installed, follow the prompt to install, then double-click "FAST.py" again.**

To customize the damage functions used by FAST to calculate losses, review these guidelines: https://github.com/nhrap-hazus/FAST/blob/master/Help/FASTDamageFunctions.pdf

![Open FAST](Images/Step3.png "Open FAST")

**4. Click "Browse to Inventory Input (.csv/.parquet)" to select your formatted building data.**

![Supply building data](Images/Step4.png "Supply building data")

**5. Select "Riverine", "CoastalA", or "CoastalV" from the "Coastal Flooding Attribute" window according to your analysis requirements. Select a flood depth dataset from the "Depth Grid" window.**

![Select flood type and flood data](Images/Step5.png "Select flood type and flood data")

**6. Click "Execute". Review the summary window after FAST finishes to confirm data were analyzed.**

![Run FAST](Images/Step6.jpg "Run FAST")

## Troubleshooting

Please reach out to the Hazus Team any time for help troubleshooting tool issues at fema-hazus-support@fema.dhs.gov.

Hazus open source tools use a centrally managed Python environment added to your machine upon installation. If you downloaded and installed FAST prior to 2021, you may need to delete your old Python environment - called "hazus_env". If you're having issues opening FAST or if the version number in your "src/__init__.py" file reads '0.0.5' or older, try one of the two options below to delete your old Python environment:


**Using Anaconda:**
1. Delete FAST from your machine.
2. Open Anaconda.
3. Select the 'hazus_env'.
4. Click the 'Remove' button.
5. A popup will appear asking you to confirm. Click the 'Remove' button. This may take a few minutes.
6. Download the latest FAST and it will create the hazus_env and install hazpy.
![Anaconda Remove Environment](Images/AnacondaRemoveEnv.jpg "Anaconda Remove hazus_env")

**Using Command Line:**
1. Delete FAST from your machine.
2. Open a command line prompt
3. Enter the following without qoutes 'conda info --envs' to see your environments. 'hazus_env' should be listed.
4. Enter the following without quotes 'conda env remove --name hazus_env' to remove the environment. This may take a few minutes.
5. Enter the following without qoutes 'conda info --envs' to see your environments again and hazus_env should not be listed.
6. Download the latest FAST and it will create the hazus_env and install hazpy.
![Command Line Remove Environment](Images/CommandLineRemoveEnv.jpg "Command Line Remove hazus_env")
