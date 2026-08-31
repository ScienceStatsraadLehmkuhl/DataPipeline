LEGS = [str(x + 1) for x in range(30)]

EXPERIMENTS = ["NAVIGATION",  "OCEANOGRAPHY", "ACOUSTIC", "METEOROLOGY", "RADIATION"]

INSTRUMENTS = { "NAVIGATION": ["GGA", "HDT", "SXN23", "VTG", "ZDA"],
                "ACOUSTIC": ["EK80-RAW"],
                "METEOROLOGY": ["GMX300", "GMX560", 'Lufft_WS100-1', 'Gill_2310037-WC76'], 
                "OCEANOGRAPHY": ["Ferrybox_CTD", "Seabird_CTD"],
                "RADIATION": ["Apogee_SI431", "Apogee_SQ522"], 
                }

VARIABLES = {   
    "NAVIGATION": {
            "GGA": ["longitude_deg", "latitude_deg"],
            "HDT": ["heading_deg_true"],
            "SXN23": ["heading", "heave","pitch","roll"],
            "VTG": ["course_over_ground_deg_mag", "course_over_ground_deg_true", "speed_over_ground_kt"],
            "ZDA":[],
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
        "HDT":{
            "Timestamp":"Timestamp",
            '"Heading, degrees true"': "heading_deg_true",
            "Heading\, degrees true": "heading_deg_true",
            "Heading, degrees true": "heading_deg_true",
            '"Heading\"': "heading_deg_true",
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
}



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