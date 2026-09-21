LEGS = [str(x + 1) for x in range(30)]

EXPERIMENTS = ["NAVIGATION",  "OCEANOGRAPHY", "ACOUSTIC", "METEOROLOGY", "RADIATION"]

INSTRUMENTS = { "NAVIGATION": ["GGA", "HDT", "SXN23", "VTG", "ZDA", "GPS-MERGED-SOURCES"],
                "ACOUSTIC": ["EK80-RAW", "EK80_echos_csv", "EK80_echos_ncdf", "EK80_CP300-ADCP"],
                "METEOROLOGY": ["GMX300", "GMX560", 'Lufft_WS100-1', 'Gill_2310037-WC76'],
                "OCEANOGRAPHY": ["Ferrybox_CTD", "Seabird_CTD"],
                "RADIATION": ["Apogee_SI431", "Apogee_SQ522"],
                }

# Some instruments' raw data carries more than one candidate timestamp
# column (e.g. Ferrybox's Aanderaa/SmartGuard export has both a root-level
# 'timestamp'/record-received time and a per-measurement 'dataTimestamp');
# this pins which one is authoritative for canonical 'time', overriding
# preprocessing.TIME_ALIAS's generic priority order for that instrument.
PREFERRED_TIME_COLUMN = {
    "Ferrybox_CTD": "dataTimestamp",
}

VARIABLES = {   
    "NAVIGATION": {
            "GGA": ["longitude_deg", "latitude_deg"],
            "HDT": ["heading_deg_true"],
            "SXN23": ["heading", "heave","pitch","roll"],
            "VTG": ["course_over_ground_deg_mag", "course_over_ground_deg_true", "speed_over_ground_kt"],
            "ZDA":[],
            "GPS-MERGED-SOURCES": ["longitude_deg", "latitude_deg"]
        },

    "METEOROLOGY": {
        "GMX300": [
            "air_temperature_C",
            "relative_humidity_pct",
            "dewpoint_temperature_C",
            "wetbulb_temperature_C",
            "station_pressure_hPa",
            "sea_level_pressure_hPa",
            "pressure_hPa",
            "heat_index_C",
            "absolute_humidity",
            "air_density",
        ],
        "GMX560": [
            "air_temperature_C",
            "relative_humidity_pct",
            "dewpoint_temperature_C",
            "wetbulb_temperature_C",
            "pressure_hPa",
            "station_pressure_hPa",
            "sea_level_pressure_hPa",
            "heat_index_C",
            "wind_chill_C",
            "compass_heading_deg",
            "relative_wind_direction_deg",
            "relative_wind_speed",
            "corrected_wind_direction_deg",
            "corrected_wind_speed",
            "relative_gust_direction_deg_wmo",
            "relative_gust_speed_wmo",
        ],
        "Lufft_WS100-1": [
            "precipitation_abs",
            "precipitation_difference",
            "precipitation_intensity_mm_h",
            "precipitation_type",
            "precipitation_intensity_mm_min",
        ],
        "Gill_2310037-WC76":[
            "Wind angle", 
            "Reference", 
            "Wind speed", 
            "Wind speed unit",
        ],
    },

    "OCEANOGRAPHY": {
        "Ferrybox_CTD": [
            "CS_conductivity",
            "CS_temperature_C",
            "CS_salinity_psu",
            "CS_density",
            "CS_sound_speed_ms",
            "o2_sensor_temperature_C",
            "o2_air_saturation_pct",
            "o2_concentration",
            "ts_temperature_C",
            "ts_pressure",
            "trilux_chlorophyll",
            "trilux_phycoerythrin",
            "trilux_turbidity",
        ],

        "Seabird_CTD": [
           "pressure_dbar",
            "temperature_C",
            "conductivity_S_m",
            "oxygen_raw_V",
            "ph",
            "fluorescence_mg_m3",
            "turbidity_NTU",
            "par_umol_m2_s",
            "beam_transmission_pct",
            "salinity_PSU",
            "oxygen_ml_L",
            "density_sigma_t",
            "sound_velocity_m_s",
            "avg_sound_velocity_m_s",
            "flag",
        ],
    },

    "RADIATION": {
        "Apogee_SI431": [
            "temperature",
            "millivolt",
            "body_temp",
        ],
        "Apogee_SQ522": [
            "par_calibrated_umol_m2_s",
            "par_immersed_umol_m2_s",
            "par_solar_umol_m2_s",
            "detector_millivolts",
            "device_status",
            "heater_status",
            "multiplier",
            "offset",
            "immersion_factor",
            "solar_multiplier",
            "running_average",
        ]
    },
    "ACOUSTIC": {
        "EK80_echos_csv": [
            "time_source",
            "latitude",
            "longitude",
            "heading",
            "pitch",
            "roll",
            "vertical_offset",
            "latitude_mru1",
            "longitude_mru1",
            "env_temperature",
            "env_salinity",
            "env_sound_speed_indicative",
            "source_raw_file",
            "sonar_model",
        ]
    }
}


def get_variables(experiment, instrument):
    return VARIABLES.get(experiment, {}).get(instrument, [])


RENAME_COLUMNS = {
    "NAVIGATION": {
        "GGA": {
            "Timestamp": "Timestamp",
            "time":"time",
            "longitude_degrees": "longitude_deg",
            "latitude_degrees": "latitude_deg",
        },
        "GPS-MERGED-SOURCES": {
            "time": "time", 
            "longitude_deg": "longitude_deg", 
            "latitude_deg": "latitude_deg"
        },

        "HDT":{
            "Timestamp":"Timestamp",
            '"Heading, degrees true"': "heading_deg_true",
            r"Heading\, degrees true": "heading_deg_true",
            "Heading, degrees true": "heading_deg_true",
            '"Heading\"': "heading_deg_true",
            "Heading,_degrees_true": "heading_deg_true",
            "time": "time",
        },
        "SXN23": {
            "Timestamp":"Timestamp",
            "time":"time",
            "Heading": "heading",
            "Heave": "heave",
            "Pitch": "pitch",
            "Roll": "roll",
        },
        "VTG":{
            "Timestamp":"Timestamp",
            "time":"time",
            "Course_over_ground,_degrees_magnetic": "course_over_ground_deg_mag", 
            "Course_over_ground,_degrees_true": "course_over_ground_deg_true", 
            "Speed_over_ground,_knots": "speed_over_ground_kt",
        },
    },
    "METEOROLOGY": {
        "GMX300": {
            # time
            "System Date and Time": "System Date and Time",
            "time":"time",
            # core atmospheric variables
            "Temperature": "air_temperature_C",
            "Relative Humidity": "relative_humidity_pct",
            "Dewpoint": "dewpoint_temperature_C",
            "Wet Bulb Temperature": "wetbulb_temperature_C",
            # pressure 
            "Pressure at Station": "station_pressure_hPa",
            "Pressure at Sea level": "sea_level_pressure_hPa",
            "Pressure": "pressure_hPa",  
            # derived atmospheric variables
            "Heat Index": "heat_index_C",
            "Absolute Humidity": "absolute_humidity",   # units depend on export
            "Air Density": "air_density",               # units depend on export
        },
        "GMX560":{
            # time
            "System Date and Time": "System Date and Time",
            "time":"time",
            # core atmospheric variables
            "Temperature": "air_temperature_C",
            "Relative Humidity": "relative_humidity_pct",
            "Dewpoint": "dewpoint_temperature_C",
            "Wet Bulb Temperature": "wetbulb_temperature_C",

            # pressure
            "Pressure": "pressure_hPa",
            "Pressure at Station": "station_pressure_hPa",
            "Pressure at Sea level": "sea_level_pressure_hPa",

            # derived
            "Heat Index": "heat_index_C",
            "Wind Chill": "wind_chill_C",

            # wind (relative / corrected / gusts)
            "Compass Heading": "compass_heading_deg",
            "Relative Wind Direction": "relative_wind_direction_deg",
            "Relative Wind Speed": "relative_wind_speed",
            "Corrected Wind Direction": "corrected_wind_direction_deg",
            "Corrected Wind Speed": "corrected_wind_speed",
            "Relative Gust Direction (WMO)": "relative_gust_direction_deg_wmo",
            "Relative Gust Speed (WMO)": "relative_gust_speed_wmo",
            },   

        "Lufft_WS100-1": {
            "timestamp": "timestamp",
            "time":"time",
            "precipitation_abs": "precipitation_abs", 
            "precipitation_difference": "precipitation_difference", 
            "precipitation_intensity_mm/h": "precipitation_intensity_mm_h", 
            "precipitation_type": "precipitation_type",
            "precipitation_intensity_mm/min":"precipitation_intensity_mm_min", 
        },

        "Gill_2310037-WC76":{
            "time":"time", 
            "timestamp": "timestamp",
            "Wind angle":"wind_angle_deg", 
            "Reference":"ref", 
            "Wind speed":"wind_speed_m_s",
        },
    },

    "OCEANOGRAPHY": {
        "Ferrybox_CTD": {
            # time
            "dataTimestamp": "dataTimestamp",
            "time":"time",
            # position
            "latitude": "latitude",
            "longitude": "longitude",
            # conductivity / salinity / density / sound speed
            "CS_Conductivity": "CS_conductivity",
            "CS_Temperature": "CS_temperature_C",
            "CS_Salinity": "CS_salinity_psu",
            "CS_Density": "CS_density",
            "CS_Soundspeed": "CS_sound_speed_ms",
            # oxygen
            "O2_Temperature": "o2_sensor_temperature_C",
            "O2_Airsaturation": "o2_air_saturation_pct",
            "O2_O2concentration": "o2_concentration",
            # additional temperature / pressure stream
            "TS_Temperature": "ts_temperature_C",
            "TS_Pressure": "ts_pressure",
            # fluorometer / optical channels (TriLux)
            "2125-051-PL-056_Chlorophyll": "trilux_chlorophyll",
            "2125-051-PL-056_Phycoerythrin": "trilux_phycoerythrin",
            "2125-051-PL-056_Turbidity": "trilux_turbidity",
        },
        "Seabird_CTD":{
            "time":"time",
            "prdM": "pressure_dbar",
            "tv268C": "temperature_C",
            "c0S/m": "conductivity_S_m",
            "sbeox0V": "oxygen_raw_V",
            "ph": "ph",
            "flECO-AFL": "fluorescence_mg_m3",
            "turbWETntu0": "turbidity_NTU",
            "par/sat/log": "par_umol_m2_s",
            "CStarTr0": "beam_transmission_pct",
            "sal00": "salinity_PSU",
            "sbeox0ML/L": "oxygen_ml_L",
            "sigma-t00": "density_sigma_t",
            "svCM": "sound_velocity_m_s",
            "avgsvCM": "avg_sound_velocity_m_s",
            "flag": "flag",
            "Ship_name_[platform_code]":"ship_name", 
            "Cruise": "cruise",
            "Station":"station",
            "Echodepth_[m]": "echo_depth_m"

        },

    },
    "RADIATION":{
        "Apogee_SI431":{
            "timestamp":"timestamp", 
            "time":"time",
            "temp":"temperature",
            "millivolt": "millivolt", 
            "body_temp":"body_temp",
        },
        "Apogee_SQ522":{
            # time
            "timestamp": "timestamp",
            "time":"time",
            # PAR / quantum outputs
            "calibrated_output_umol_m2_s": "par_calibrated_umol_m2_s",
            "immersed_output_umol_m2_s": "par_immersed_umol_m2_s",
            "solar_output_umol_m2_s": "par_solar_umol_m2_s",

            # raw signal
            "detector_millivolts": "detector_millivolts",

            # device metadata / configuration / status
            "device_status": "device_status",
            "heater_status": "heater_status",
            "multiplier": "multiplier",
            "offset": "offset",
            "immersion_factor": "immersion_factor",
            "solar_multiplier": "solar_multiplier",
            "running_average": "running_average",
        },
    },
    "ACOUSTIC": {
        "EK80_echos_csv": {
            "timestamp": "timestamp",
            "time": "time",
            "time_source": "time_source",
            "latitude": "latitude",
            "longitude": "longitude",
            "heading": "heading",
            "pitch": "pitch",
            "roll": "roll",
            "vertical_offset": "vertical_offset",
            "latitude_mru1": "latitude_mru1",
            "longitude_mru1": "longitude_mru1",
            "env_temperature": "env_temperature",
            "env_salinity": "env_salinity",
            "env_sound_speed_indicative": "env_sound_speed_indicative",
            "source_raw_file": "source_raw_file",
            "sonar_model": "sonar_model",
        },
    },
}



# WMO code table 4680 (wawa): present weather reported by an automatic weather
# station. Reserved codes are omitted (except 69); group headings (21, 27, 30, 40, 50, 60,
# 70, 80, 90) are kept since a station can report them as-is.
WAWA_CODES = {
    0: "No precipitation",
    1: "Clouds dissolving or becoming less developed (past hour)",
    2: "State of sky unchanged (past hour)",
    3: "Clouds forming or developing (past hour)",
    4: "Haze, smoke or dust, visibility >= 1 km",
    5: "Haze, smoke or dust, visibility < 1 km",
    10: "Mist",
    11: "Diamond dust",
    12: "Distant lightning",
    18: "Squalls",
    20: "Fog (past hour)",
    21: "Precipitation (past hour)",
    22: "Drizzle (not freezing) or snow grains (past hour)",
    23: "Rain (not freezing) (past hour)",
    24: "Snow (past hour)",
    25: "Freezing drizzle or freezing rain (past hour)",
    26: "Thunderstorm (past hour)",
    27: "Blowing or drifting snow or sand",
    28: "Blowing or drifting snow or sand, visibility >= 1 km",
    29: "Blowing or drifting snow or sand, visibility < 1 km",
    30: "Fog",
    31: "Fog or ice fog in patches",
    32: "Fog or ice fog, thinner",
    33: "Fog or ice fog, no change",
    34: "Fog or ice fog, thicker",
    35: "Fog, depositing rime",
    40: "Precipitation",
    41: "Precipitation, slight or moderate",
    42: "Precipitation, heavy",
    43: "Liquid precipitation, slight or moderate",
    44: "Liquid precipitation, heavy",
    45: "Solid precipitation, slight or moderate",
    46: "Solid precipitation, heavy",
    47: "Freezing precipitation, slight or moderate",
    48: "Freezing precipitation, heavy",
    50: "Drizzle",
    51: "Drizzle, not freezing, slight",
    52: "Drizzle, not freezing, moderate",
    53: "Drizzle, not freezing, heavy",
    54: "Drizzle, freezing, slight",
    55: "Drizzle, freezing, moderate",
    56: "Drizzle, freezing, heavy",
    57: "Drizzle and rain, slight",
    58: "Drizzle and rain, moderate or heavy",
    60: "Rain",
    61: "Rain, not freezing, slight",
    62: "Rain, not freezing, moderate",
    63: "Rain, not freezing, heavy",
    64: "Rain, freezing, slight",
    65: "Rain, freezing, moderate",
    66: "Rain, freezing, heavy",
    67: "Rain (or drizzle) and snow, slight",
    68: "Rain (or drizzle) and snow, moderate or heavy",
    69: "Rain, drizzle, Snow",  # reserved in the WMO table, but the Lufft reports it
    70: "Snow",
    71: "Snow, slight",
    72: "Snow, moderate",
    73: "Snow, heavy",
    74: "Ice pellets, slight",
    75: "Ice pellets, moderate",
    76: "Ice pellets, heavy",
    77: "Snow grains",
    78: "Ice crystals",
    80: "Showers or intermittent precipitation",
    81: "Rain showers, slight",
    82: "Rain showers, moderate",
    83: "Rain showers, heavy",
    84: "Rain showers, violent",
    85: "Snow showers, slight",
    86: "Snow showers, moderate",
    87: "Snow showers, heavy",
    89: "Hail",
    90: "Thunderstorm",
    91: "Thunderstorm, slight or moderate, no precipitation",
    92: "Thunderstorm, slight or moderate, rain and/or snow showers",
    93: "Thunderstorm, slight or moderate, hail",
    94: "Thunderstorm, heavy, no precipitation",
    95: "Thunderstorm, heavy, rain and/or snow showers",
    96: "Thunderstorm, heavy, hail",
    99: "Tornado",
}

# Categorical (code-valued) columns: these can't be averaged, so subsample()
# takes the dominant code per time bin instead (see data_processing_sensors),
# and the plotters show the names on the y axis instead of the codes.
CATEGORICAL_VARIABLES = {
    "METEOROLOGY": {
        "Lufft_WS100-1": {"precipitation_type": WAWA_CODES},
    },
}


def get_categorical_codes(experiment, instrument):
    """{column: {code: name}} for the code-valued columns of one instrument ({} if none)."""
    return CATEGORICAL_VARIABLES.get(experiment, {}).get(instrument, {})


PLOT_LABELS = {
    "NAVIGATION": {
        "GGA": {
            "time": "Date and time (UTC)",
            "latitude_deg": "Latitude (°)",
            "longitude_deg": "Longitude (°)",
        },
        "HDT": {
            "time": "Date and time (UTC)",
            "heading_deg_true": "True heading (°)",
        },
        "SXN23": {
            "time": "Date and time (UTC)",
            "heading": "Heading (°)",
            "heave": "Heave (m)",
            "pitch": "Pitch (°)",
            "roll": "Roll (°)", 
        },
        "VTG": {
            "time": "Date and time (UTC)",
            "course_over_ground_deg_mag": "Magnetic course over ground (°)", 
            "course_over_ground_deg_true": "True course over ground (°)",
            "speed_over_ground_kt": "Speed over ground (knots)",
        }, 
    }, 
    "METEOROLOGY": {
        "GMX300": {
            "time": "Date and time (UTC)",

            "air_temperature_C": "Air temperature (°C)",
            "relative_humidity_pct": "Relative humidity (%)",
            "dewpoint_temperature_C": "Dew-point temperature (°C)",
            "wetbulb_temperature_C": "Wet-bulb temperature (°C)",

            "station_pressure_hPa": "Station pressure (hPa)",
            "sea_level_pressure_hPa": "Sea-level pressure (hPa)",
            "pressure_hPa": "Pressure (hPa)",

            "heat_index_C": "Heat index (°C)",
            "absolute_humidity": "Absolute humidity",  # add units once confirmed
            "air_density": "Air density",              # add units once confirmed
        },

        "GMX560": {
            "time": "Date and time (UTC)",

            "air_temperature_C": "Air temperature (°C)",
            "relative_humidity_pct": "Relative humidity (%)",
            "dewpoint_temperature_C": "Dew-point temperature (°C)",
            "wetbulb_temperature_C": "Wet-bulb temperature (°C)",

            "station_pressure_hPa": "Station pressure (hPa)",
            "sea_level_pressure_hPa": "Sea-level pressure (hPa)",
            "pressure_hPa": "Pressure (hPa)",

            "heat_index_C": "Heat index (°C)",
            "wind_chill_C": "Wind chill (°C)",

            "compass_heading_deg": "Compass heading (°)",
            "relative_wind_direction_deg": "Relative wind direction (°)",
            "relative_wind_speed": "Relative wind speed",
            "corrected_wind_direction_deg": "Corrected wind direction (°)",
            "corrected_wind_speed": "Corrected wind speed",
            "relative_gust_direction_deg_wmo": "Relative gust direction (WMO) (°)",
            "relative_gust_speed_wmo": "Relative gust speed (WMO)",

        },

        "Lufft_WS100-1": {
            "time": "Date and time (UTC)",
            "precipitation_abs": "Precipitation (accumulated)",
            "precipitation_difference": "Precipitation (difference)",
            "precipitation_intensity_mm_h": "Precipitation intensity (mm/h)",
            "precipitation_intensity_mm_min": "Precipitation intensity (mm/min)",
            "precipitation_type": "Precipitation type",
        },
    },

    "OCEANOGRAPHY": {
        "Ferrybox_CTD": {
            "time": "Date and time (UTC)",

            "latitude_deg": "Latitude (°)",
            "longitude_deg": "Longitude (°)",

            "CS_conductivity": "Conductivity",          #Unit?
            "CS_temperature_C": "Water temperature (°C)",
            "CS_salinity_psu": "Salinity (PSU)",
            "CS_density": "Seawater density",
            "CS_sound_speed_ms": "Sound speed (m.s⁻¹)",

            "o2_sensor_temperature_C": "Water temperature (°C)",
            "o2_air_saturation_pct": "Oxygen saturation (%)",           #Or is it O2 saturation in the water?
            "o2_concentration": "Oxygen concentration",

            "ts_temperature_C": "Temperature (°C)",
            "ts_pressure": "Water pressure in the sensor chamber (kPa)",

            "trilux_chlorophyll": "Chlorophyll A (µg.L⁻¹)",
            "trilux_phycoerythrin": "Phycoerythrin (µg.L⁻¹ ??)",
            "trilux_turbidity": "Turbidity (NTU)",
        },
        "Seabird_CTD": {
            "pressure_dbar": "Depth (m) from pressure strain gauge (dbar)",
            "temperature_C": "Temperature (IPTS-68, °C)",
            "conductivity_S_m": "Conductivity (S.m⁻¹)",
            "oxygen_raw_V": "Oxygen raw signal, SBE 43 (V)",
            "ph": "pH",
            "fluorescence_mg_m3": "Fluorescence, WET Labs ECO-AFL/FL (mg.m⁻³)",
            "turbidity_NTU": "Turbidity, WET Labs ECO (NTU)",
            "par_umol_m2_s": "Photosynthetically active radiation, Satlantic (µmol photons m⁻² s⁻¹)",
            "beam_transmission_pct": "Beam transmission, WET Labs C-Star (%)",
            "salinity_PSU": "Salinity, practical salinity (PSU)",
            "oxygen_ml_L": "Dissolved oxygen, SBE 43 (mL.L⁻¹)",
            "density_sigma_t": "Density anomaly (sigma-t, kg.m⁻³)",
            "sound_velocity_m_s": "Sound velocity, Chen–Millero (m.s⁻¹)",
            "avg_sound_velocity_m_s": "Average sound velocity, Chen–Millero (m.s⁻¹)",
            "flag": "Data quality flag",
        },
    },

    "RADIATION": {
        "Apogee_SI431": {
            "time": "Date and time (UTC)",
            "temperature": "Temperature (°C)",
            "millivolt": "Signal (mV)",
            "body_temp": "Body temperature (°C)",
        },

        "Apogee_SQ522": {
            "time": "Date and time (UTC)",

            "par_calibrated_umol_m2_s": "PAR (calibrated) (µmol.m⁻².s⁻¹)",
            "par_immersed_umol_m2_s": "PAR (immersed) (µmol.m⁻².s⁻¹)",
            "par_solar_umol_m2_s": "PAR (solar) (µmol.m⁻².s⁻¹)",

            "detector_millivolts": "Detector signal (mV)",

            "device_status": "Device status",
            "heater_status": "Heater status",
            "firmware_version": "Firmware version",
            "model_number": "Model number",
            "serial_number": "Serial number",
            "multiplier": "Multiplier",
            "offset": "Offset",
            "immersion_factor": "Immersion factor",
            "solar_multiplier": "Solar multiplier",
            "running_average": "Running average",
        },
    },
}