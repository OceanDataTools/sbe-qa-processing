import pytest
from seabirdscientific.instrument_data import Sensors

from sbe_qa_processing.xmlcon import XmlconError, parse_xmlcon

TEMPERATURE = """<TemperatureSensor SensorID="55"><SerialNumber>{sn}</SerialNumber>
<CalibrationDate>08-May-2025</CalibrationDate><G>4.3e-3</G><H>6.3e-4</H><I>1.9e-5</I>
<J>1.5e-6</J><F0>1000.0</F0><Slope>1.0</Slope><Offset>0.001</Offset></TemperatureSensor>"""
CONDUCTIVITY = """<ConductivitySensor SensorID="3"><SerialNumber>{sn}</SerialNumber>
<Coefficients equation="1"><G>-10.0</G><H>1.23</H><I>-8.2e-4</I><J>1.2e-4</J>
<CPcor>-9.57e-8</CPcor><CTcor>3.25e-6</CTcor><WBOTC>0</WBOTC></Coefficients>
<Slope>1.0</Slope><Offset>0.0</Offset></ConductivitySensor>"""
PRESSURE = """<PressureSensor SensorID="45"><SerialNumber>0381</SerialNumber><C1>-5.1e4</C1>
<C2>0.19</C2><C3>0.0155</C3><D1>0.042</D1><D2>0</D2><T1>30.0</T1><T2>-3e-4</T2><T3>4e-6</T3>
<T4>2.6e-9</T4><Slope>0.9999402</Slope><Offset>0.8834</Offset><T5>0</T5><AD590M>0.0128</AD590M>
<AD590B>-9.415</AD590B></PressureSensor>"""


def write_xmlcon(tmp_path, name="SBE 911plus/917plus CTD", secondary=True, nmea=1):
    sensors = [TEMPERATURE.format(sn="4924"), CONDUCTIVITY.format(sn="4537"), PRESSURE]
    if secondary:
        sensors += [TEMPERATURE.format(sn="2059"), CONDUCTIVITY.format(sn="4605")]
    else:
        sensors += ['<NotInUse SensorID="27"/>', '<NotInUse SensorID="27"/>']
    array = "".join(f'<Sensor index="{i}">{s}</Sensor>' for i, s in enumerate(sensors))
    path = tmp_path / "cast.XMLCON"
    path.write_text(
        f"""<SBE_InstrumentConfiguration><Instrument><Name>{name}</Name>
<FrequencyChannelsSuppressed>0</FrequencyChannelsSuppressed>
<VoltageWordsSuppressed>1</VoltageWordsSuppressed><ScansToAverage>1</ScansToAverage>
<SurfaceParVoltageAdded>0</SurfaceParVoltageAdded><ScanTimeAdded>0</ScanTimeAdded>
<NmeaPositionDataAdded>{nmea}</NmeaPositionDataAdded><NmeaDepthDataAdded>0</NmeaDepthDataAdded>
<NmeaTimeAdded>0</NmeaTimeAdded><SensorArray>{array}</SensorArray></Instrument>
</SBE_InstrumentConfiguration>"""
    )
    return path


def test_coefficients_and_slope_offset(tmp_path):
    config = parse_xmlcon(write_xmlcon(tmp_path))
    coefs, slope, offset = config.temperature_coefficients("temperature2")
    assert (coefs.g, coefs.f0, slope, offset) == (4.3e-3, 1000.0, 1.0, 0.001)
    coefs, _, _ = config.conductivity_coefficients()
    assert (coefs.g, coefs.ctcor, coefs.wbotc) == (-10.0, 3.25e-6, 0)
    pressure = config.pressure_coefficients()
    # The digiquartz slope/offset must reach seabirdscientific
    assert (pressure.slope, pressure.offset) == (0.9999402, 0.8834)
    assert config.sensor("temperature").serial_number == "4924"
    assert config.has_secondary_pair


def test_enabled_sensors_follow_suppression_and_nmea(tmp_path):
    config = parse_xmlcon(write_xmlcon(tmp_path))
    enabled = config.enabled_sensors()
    assert Sensors.SecondaryConductivity in enabled
    assert Sensors.ExtVolt5 in enabled and Sensors.ExtVolt6 not in enabled  # one word suppressed
    assert Sensors.nmeaLocation in enabled


def test_without_secondary_pair(tmp_path):
    config = parse_xmlcon(write_xmlcon(tmp_path, secondary=False))
    assert not config.has_secondary_pair
    with pytest.raises(XmlconError):
        config.temperature_coefficients("temperature2")


def test_rejects_other_instruments(tmp_path):
    with pytest.raises(XmlconError, match="SBE 21"):
        parse_xmlcon(write_xmlcon(tmp_path, name="SBE 21 Seacat Thermosalinograph"))


def test_rejects_invalid_xml(tmp_path):
    path = tmp_path / "bad.XMLCON"
    path.write_text("<SBE_InstrumentConfiguration><Instrument>")
    with pytest.raises(XmlconError, match="not valid XML"):
        parse_xmlcon(path)
