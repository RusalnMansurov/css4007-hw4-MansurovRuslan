from tools import lookup, calculate

def test_lookup_population(): assert lookup('Brightwater','population_2025')['value']==44675
def test_lookup_correction():
    x=lookup('Brightwater','area_km2'); assert x['value']==921.4 and 'NR-16' in x['sources']
def test_lookup_typo(): assert 'Ossbridge' in lookup('Ossbrige','area_km2')['suggestions']
def test_lookup_similar(): assert lookup('Ossbridge','population_2025')['value']!=lookup('Ossford','population_2025')['value']
def test_lookup_archived(): assert lookup('Tarnvale','population_2025')['sources']==['NR-03']
def test_lookup_units(): assert lookup('Lindenmoor','water_2025_m3')['value']==2151400
def test_lookup_tariff(): assert lookup('Millbrook','water_tariff_2025_tenge_per_m3')['value']==142.35
def test_calculate_product(): assert abs(calculate('48317 * 1.0947')['result']-52892.6199)<1e-6
def test_calculate_import(): assert 'error' in calculate("__import__('os').system('echo hi')")
def test_calculate_attribute(): assert 'error' in calculate('(1).__class__')
def test_calculate_power(): assert 'error' in calculate('9**9**9')
def test_calculate_zero(): assert calculate('2/0')['error']=='Division by zero'
def test_calculate_normal(): assert calculate('(37203/758.6)-(44675/921.4)')['result']>0


def test_lookup_unknown_field():
    assert 'error' in lookup('Brightwater', 'not_a_field')

def test_lookup_invalid_type():
    assert 'error' in lookup(None, 'area_km2')

def test_lookup_missing_district():
    assert 'error' in lookup('NotARealDistrict', 'area_km2')

def test_calculate_invalid_syntax():
    assert 'error' in calculate('2 + (')

def test_calculate_rejects_long_expression():
    assert 'error' in calculate('1+' * 200 + '1')

def test_calculate_rejects_non_numeric():
    assert 'error' in calculate('"hello"')
