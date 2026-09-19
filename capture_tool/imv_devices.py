# IMV ABI device structures; see THIRD_PARTY.md.
import ctypes
IMV_MAX_STRING_LENGTH = 256

class _IMVGigEInterfaceInfo(ctypes.Structure):
    _fields_ = [
        ("description", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("macAddress", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("ipAddress", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("subnetMask", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("defaultGateWay", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("chReserved", (ctypes.c_char * IMV_MAX_STRING_LENGTH) * 5),
    ]

class _IMVUsbInterfaceInfo(ctypes.Structure):
    _fields_ = [
        ("description", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("vendorID", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("deviceID", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("subsystemID", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("revision", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("speed", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("chReserved", (ctypes.c_char * IMV_MAX_STRING_LENGTH) * 4),
    ]

class _IMVGigEDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("nIpConfigOptions", ctypes.c_uint),
        ("nIpConfigCurrent", ctypes.c_uint),
        ("nReserved", ctypes.c_uint * 3),
        ("macAddress", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("ipAddress", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("subnetMask", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("defaultGateWay", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("protocolVersion", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("ipConfiguration", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("strReserved", (ctypes.c_char * IMV_MAX_STRING_LENGTH) * 6),
    ]

class _IMVUsbDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("bLowSpeedSupported", ctypes.c_bool),
        ("bFullSpeedSupported", ctypes.c_bool),
        ("bHighSpeedSupported", ctypes.c_bool),
        ("bSuperSpeedSupported", ctypes.c_bool),
        ("bDriverInstalled", ctypes.c_bool),
        ("boolReserved", ctypes.c_bool * 3),
        ("Reserved", ctypes.c_uint * 4),
        ("configurationValid", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("genCPVersion", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("u3vVersion", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("deviceGUID", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("familyName", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("u3vSerialNumber", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("speed", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("maxPower", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("chReserved", (ctypes.c_char * IMV_MAX_STRING_LENGTH) * 4),
    ]

class _IMVInterfaceInfo(ctypes.Union):
    _fields_ = [
        ("gigeInterfaceInfo", _IMVGigEInterfaceInfo),
        ("usbInterfaceInfo", _IMVUsbInterfaceInfo),
    ]

class _IMVDeviceSpecificInfo(ctypes.Union):
    _fields_ = [
        ("gigeDeviceInfo", _IMVGigEDeviceInfo),
        ("usbDeviceInfo", _IMVUsbDeviceInfo),
    ]

class _IMVDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("nCameraType", ctypes.c_int),
        ("nCameraReserved", ctypes.c_int * 5),
        ("cameraKey", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("cameraName", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("serialNumber", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("vendorName", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("modelName", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("manufactureInfo", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("deviceVersion", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("cameraReserved", (ctypes.c_char * IMV_MAX_STRING_LENGTH) * 5),
        ("DeviceSpecificInfo", _IMVDeviceSpecificInfo),
        ("nInterfaceType", ctypes.c_int),
        ("nInterfaceReserved", ctypes.c_int * 5),
        ("interfaceName", ctypes.c_char * IMV_MAX_STRING_LENGTH),
        ("interfaceReserved", (ctypes.c_char * IMV_MAX_STRING_LENGTH) * 5),
        ("InterfaceInfo", _IMVInterfaceInfo),
    ]
