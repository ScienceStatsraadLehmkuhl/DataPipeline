#!/usr/bin/env python3
"""
Script to extract zip and convert to multiple csv files. 

"""
import csv
import os
from typing import Optional
import zipfile
import shutil
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd
from DataPipeline.globals import VARIABLES

# Register namespace to handle it properly
ET.register_namespace('', 'http://www.aadi.no/RTOutSchema')

def parse_timestamp(timestamp_str: str) -> datetime:
    """Parse ISO format timestamp and return timezone-naive datetime"""
    if timestamp_str.endswith('Z'):
        timestamp_str = timestamp_str[:-1]
    try:
        return datetime.fromisoformat(timestamp_str)
    except ValueError:
        return datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S.%f')

def format_timestamp_for_filename(dt: datetime) -> str:
    return dt.strftime('%Y%m%d%H%M%S')

def strip_namespace(tag):
    return tag.split('}')[1] if '}' in tag else tag

def find_element(parent, tag_name):
    for elem in parent:
        if strip_namespace(elem.tag) == tag_name:
            return elem
    return None

def findall_elements(parent, tag_name):
    results = []
    for elem in parent:
        if strip_namespace(elem.tag) == tag_name:
            results.append(elem)
    return results

def extract_gps_coordinates(system_data: ET.Element) -> tuple[float, float]:
    latitude = None
    longitude = None
    
    if system_data is None:
        return 0.0, 0.0
    
    parameters = find_element(system_data, 'Parameters')
    if parameters is not None:
        for point in findall_elements(parameters, 'Point'):
            point_id = point.get('ID')
            if point_id == '7':
                value_elem = find_element(point, 'Value')
                if value_elem is not None and value_elem.text:
                    latitude = float(value_elem.text)
            elif point_id == '8':
                value_elem = find_element(point, 'Value')
                if value_elem is not None and value_elem.text:
                    longitude = float(value_elem.text)
    
    return longitude or 0.0, latitude or 0.0

def extract_sensor_measurements(sensor_data: ET.Element) -> dict:
    measurements = {}
    parameters = find_element(sensor_data, 'Parameters')
    
    if parameters is not None:
        for point in findall_elements(parameters, 'Point'):
            descr = point.get('Descr', '').lower().replace(' ', '_')
            unit = point.get('Unit', '')
            range_min = point.get('RangeMin', '')
            range_max = point.get('RangeMax', '')
            
            value_elem = find_element(point, 'Value')
            if value_elem is not None and value_elem.text:
                try:
                    value = float(value_elem.text)
                    measurements[descr] = {
                        'value': value,
                        'unit': unit,
                        'range': f"{range_min}-{range_max}" if range_min and range_max else None
                    }
                except ValueError:
                    pass
    
    return measurements

def extract_system_data(system_data: ET.Element) -> dict:
    data = {
        'inputVoltage': {},
        'cpuCoreActive': {},
        'memoryUsed': {},
        'internalTemperature': {},
        'gps': {}
    }
    
    if system_data is None:
        return data
    
    parameters = find_element(system_data, 'Parameters')
    if parameters is not None:
        for point in findall_elements(parameters, 'Point'):
            point_id = point.get('ID')
            descr = point.get('Descr', '')
            unit = point.get('Unit', '')
            
            value_elem = find_element(point, 'Value')
            if value_elem is not None and value_elem.text:
                try:
                    if point_id == '0' and 'Input Voltage' in descr:
                        data['inputVoltage']['current'] = float(value_elem.text)
                        data['inputVoltage']['unit'] = unit
                    elif point_id == '1' and 'Input Voltage Avg' in descr:
                        data['inputVoltage']['average'] = float(value_elem.text)
                    elif point_id == '2' and 'Input Voltage Min' in descr:
                        data['inputVoltage']['minimum'] = float(value_elem.text)
                    elif point_id == '3' and 'Input Voltage Max' in descr:
                        data['inputVoltage']['maximum'] = float(value_elem.text)
                    elif point_id == '4' and 'CPU Core Active' in descr:
                        data['cpuCoreActive'] = {'value': float(value_elem.text), 'unit': unit}
                    elif point_id == '5' and 'Memory Used' in descr:
                        data['memoryUsed'] = {'value': int(value_elem.text), 'unit': unit}
                    elif point_id == '6' and 'Internal Temperature' in descr:
                        data['internalTemperature'] = {'value': float(value_elem.text), 'unit': unit}
                    elif point_id == '7':
                        data['gps']['latitude'] = float(value_elem.text)
                    elif point_id == '8':
                        data['gps']['longitude'] = float(value_elem.text)
                except (ValueError, TypeError):
                    pass
    
    return data

def find_in_tree(root, tag_name):
    for elem in root.iter():
        if strip_namespace(elem.tag) == tag_name:
            return elem
    return None

def findall_in_tree(root, tag_name):
    results = []
    for elem in root.iter():
        if strip_namespace(elem.tag) == tag_name:
            results.append(elem)
    return results

def process_xml_file_old(xml_path: Path, from_time: Optional[datetime] = None, 
                         to_time: Optional[datetime] = None) -> Optional[dict]:
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        device_info = {
            'deviceId': root.get('ID'),
            'sessionId': root.get('SessionID'),
            'deviceDescription': root.get('Descr'),
            'serialNo': root.get('SerialNo'),
            'productName': root.get('ProdName'),
            'deviceType': root.get('DeviceType'),
            'protocolVersion': root.get('ProtocolVer')
        }
        
        time_elem = find_element(root, 'Time')
        time_received_elem = find_element(root, 'TimeReceived')
        
        if time_elem is None or time_elem.text is None:
            return None
            
        timestamp = parse_timestamp(time_elem.text)
        if from_time and timestamp < from_time:
            return None
        if to_time and timestamp > to_time:
            return None
        
        system_info = {}
        system_info_elem = find_element(root, 'SystemInformation')
        if system_info_elem:
            for sys_info in findall_elements(system_info_elem, 'SystemInfo'):
                info_id = sys_info.get('ID')
                if sys_info.text:
                    if info_id == '10':
                        system_info['owner'] = sys_info.text
                    elif info_id == '13':
                        system_info['location'] = sys_info.text
                    elif info_id == '12':
                        system_info['reference'] = sys_info.text
                    elif info_id == '21':
                        system_info['geoposition'] = sys_info.text
                    elif info_id == '30':
                        system_info['verticalposition'] = sys_info.text
        
        data_elem = find_in_tree(root, 'Data')
        if data_elem is None:
            return None
        
        data_time_elem = find_element(data_elem, 'Time')
        record_elem = find_element(data_elem, 'RecordNumber')
        data_timestamp = data_time_elem.text if data_time_elem is not None else None
        record_number = int(record_elem.text) if record_elem is not None and record_elem.text else None
        
        specified_interval = find_element(data_elem, 'SpecifiedInterval')
        actual_interval = find_element(data_elem, 'ActualInterval')
        
        intervals = {
            'specified': float(specified_interval.text) if specified_interval is not None and specified_interval.text else None,
            'actual': float(actual_interval.text) if actual_interval is not None and actual_interval.text else None
        }
        
        system_data_elem = find_in_tree(data_elem, 'SystemData')
        longitude, latitude = extract_gps_coordinates(system_data_elem)


        
        sensors = {}
        for sensor_data in findall_elements(data_elem, 'SensorData'):
            sensor_id = sensor_data.get('ID')
            sensor_info = {
                'id': sensor_id,
                'serialNo': sensor_data.get('SerialNo'),
                'description': sensor_data.get('Descr'),
                'productName': sensor_data.get('ProdName'),
                'measurements': extract_sensor_measurements(sensor_data)
            }
            if sensor_data.get('VerticalPosition'):
                sensor_info['verticalPosition'] = sensor_data.get('VerticalPosition')
            if sensor_data.get('GeoPosition'):
                sensor_info['geoPosition'] = sensor_data.get('GeoPosition')
            
            if 'tide' in sensor_info['description'].lower():
                sensor_key = 'tide_sensor_' + sensor_info['serialNo']
            elif 'oxygen' in sensor_info['description'].lower():
                sensor_key = 'oxygen_optode_' + sensor_info['serialNo']
            elif 'conductivity' in sensor_info['description'].lower():
                sensor_key = 'conductivity_sensor_' + sensor_info['serialNo']
            else:
                sensor_key = sensor_id
            
            sensors[sensor_key] = sensor_info
        
        system_data = extract_system_data(system_data_elem) if system_data_elem is not None else {}
        
        flattened_properties = {
            **device_info,
            'sourceFile': xml_path.name,
            'timestamp': time_elem.text,
            'timeReceived': time_received_elem.text if time_received_elem is not None else None,
            **system_info,
            'dataTimestamp': data_timestamp,
            'recordNumber': record_number,
            'interval_specified': intervals.get('specified'),
            'interval_actual': intervals.get('actual')
        }
        
        flattened_properties['latitude']  = latitude
        flattened_properties['longitude'] = longitude
        
        sensor_abbreviations = {
            'tide_sensor': 'TS',
            'oxygen_optode': 'O2',
            'conductivity_sensor': 'CS'
        }
        
        for sensor_key, sensor_info in sensors.items():
            abbrev = sensor_key
            for full_name, short_name in sensor_abbreviations.items():
                if sensor_key.startswith(full_name):
                    abbrev = short_name
                    break
            flattened_properties[f'{sensor_key}_id'] = sensor_info['id']
            flattened_properties[f'{sensor_key}_description'] = sensor_info['description']
            for measurement_name, measurement_data in sensor_info['measurements'].items():
                formatted_measurement = ''.join(word.capitalize() for word in measurement_name.split('_'))
                prop_name = f'{abbrev}_{formatted_measurement}'
                flattened_properties[prop_name] = measurement_data['value']
                flattened_properties[f'{prop_name}_unit'] = measurement_data['unit']
                if measurement_data.get('range'):
                    flattened_properties[f'{prop_name}_range'] = measurement_data['range']
        
        if system_data.get('inputVoltage'):
            flattened_properties['voltage_current'] = system_data['inputVoltage'].get('current')
            flattened_properties['voltage_avg'] = system_data['inputVoltage'].get('average')
            flattened_properties['voltage_min'] = system_data['inputVoltage'].get('minimum')
            flattened_properties['voltage_max'] = system_data['inputVoltage'].get('maximum')
        if system_data.get('cpuCoreActive'):
            flattened_properties['cpu_active'] = system_data['cpuCoreActive'].get('value')
        if system_data.get('memoryUsed'):
            flattened_properties['memory_used'] = system_data['memoryUsed'].get('value')
        if system_data.get('internalTemperature'):
            flattened_properties['internal_temp'] = system_data['internalTemperature'].get('value')
        
        feature = {
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [longitude, latitude]
            },
            'properties': flattened_properties
        }
        
        return feature
        
    except Exception as e:
        print(f"Error processing {xml_path}: {str(e)}")
        return None
 

def extract_data_from_xml(xml_file_path):
    """
    Extract time, latitude, and longitude from an XML file.
   
    Args:
        xml_file_path (str): Path to the XML file
       
    Returns:
        dict: Dictionary with time, latitude, and longitude, or None if data not found
    """
    try:
        # Parse the XML file
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
       
        # Define namespace (from the XML)
        namespace = {'ns': 'http://www.aadi.no/RTOutSchema'}
       
        # Extract time from Data section
        time_element = root.find('.//ns:Data/ns:Time', namespace)
        if time_element is None:
            print(f"Warning: Time not found in {xml_file_path}")
            return None
       
        time_str = time_element.text
       
        # Find GPS data in SystemData section
        lat_element = root.find('.//ns:SystemData[@ID="SYSDATA-0"]//ns:Point[@ID="7"]/ns:Value', namespace)
        lon_element = root.find('.//ns:SystemData[@ID="SYSDATA-0"]//ns:Point[@ID="8"]/ns:Value', namespace)
       
        if lat_element is None or lon_element is None:
            print(f"Warning: GPS coordinates not found in {xml_file_path}")
            return None
       
        latitude = float(lat_element.text)
        longitude = float(lon_element.text)
       
        return {
            'time': time_str,
            'latitude': latitude,
            'longitude': longitude
        }
       
    except ET.ParseError as e:
        print(f"Error parsing XML file {xml_file_path}: {e}")
        return None
    except Exception as e:
        print(f"Error processing file {xml_file_path}: {e}")
        return None
 
def process_xml_files(input_path, output_csv_path):
    """
    Process XML file(s) and write results to CSV.
   
    Args:
        input_path (str): Path to directory containing XML files
        output_csv_path (str): Path to output CSV file

    Return
    ------
    data_rows: dict
        The reads from the zip
    keywords: list of str
        The names of the columns

    """
    data_rows = []
    keywords = []
    
    # Directory - process all XML files
    xml_files = [f for f in os.listdir(input_path) if f.lower().endswith('.xml')]
    if not xml_files:
        print(f"No XML files found in directory: {input_path}")
        return

    for xml_file in sorted(xml_files):
        xml_path = os.path.join(input_path, xml_file)
        #print(f"Processing file: {xml_path}")
        data = None
        data = process_xml_file_old(Path(xml_path))
        if data:
            data_rows.append(data['properties'])            
            keywords = list(set(list(data['properties'].keys()) + keywords))
    
        df = pd.DataFrame(data_rows)
        output_file = Path(output_csv_path).with_suffix(".csv")
        df.to_csv(output_file, index=False)    
   
    print(f"      Successfully wrote {len(data_rows)} records to {output_file.name}")


def extract_zip(zip_path, dest_folder):
    """
    Extracts the contents of a ZIP file into the specified folder.

    :param zip_path: Path to the .zip file
    :param dest_folder: Folder where contents should be extracted
    """
    # Ensure destination folder exists
    os.makedirs(dest_folder, exist_ok=True)

    # Extract the zip file
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(dest_folder)

    #print(f"Extracted '{zip_path}' to '{dest_folder}'")


def make_temp_path():
    """
    Create a fresh temporary directory at /tmp/ferrybox_temp.

    This function ensures that the directory `/tmp/ferrybox_temp` exists
    and is empty. If the directory already exists, it is deleted along
    with all of its contents before being recreated. The function then
    returns the path as a string.

    Returns:
        Path: obj
        The path to the cleaned and recreated temporary directory.
    """
    temp_dir = Path("/tmp/ferrybox_temp")
    
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
        
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    return temp_dir
  

def convert_zips_to_csvs(filename, output_folder_name):
    """
    Docstring for convert_zip_to_csv
    
    :param filename: Description
    :param output_file: Description
    """
    temp_extract_files_path = make_temp_path()

    Path(output_folder_name).mkdir(parents=True, exist_ok=True)
    b = Path(output_folder_name)
    c = Path(filename).with_suffix(".csv").name
    new_filename = str(Path.joinpath(b, c))

    extract_zip(filename, temp_extract_files_path)
    process_xml_files(temp_extract_files_path, new_filename)
    

def read_csv(csv_path):
    """
    Read a CSV file, print all rows, and return the list of column headers.

    Args:
        csv_path (str or Path): Path to the CSV file.

    Returns:
        dict: the csv content.
        list: A list of column headers ("keywords").
    """
    csv_path = Path(csv_path)
    data_raw = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        
        keywords = reader.fieldnames  # column headers

        for row in reader:
            data_raw.append(row)

    return data_raw, keywords

















