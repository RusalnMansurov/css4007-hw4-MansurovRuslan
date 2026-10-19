"""HW4: bounded document lookup and safe arithmetic. No API key required."""
import ast
import difflib
import json
import math
import operator
import re
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / 'data' / 'corpus.jsonl'
FIELDS = ('population_2020','population_2025','area_km2','founded_year','budget_2025_million_tenge','water_2025_m3','schools_2025_26','pupils_2025_26','paved_road_km','water_tariff_2025_tenge_per_m3')
UNITS = ('residents','residents','km2','year','million tenge','m3','schools','pupils','km','tenge/m3')
NUMBER = r'([\d,]+(?:\.\d+)?)'


def _number(s):
    return float(s.replace(',', ''))


def _match(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _number(match.group(1))
    raise ValueError('Field unavailable or invalid')


def _extract(text, field):
    p = NUMBER
    patterns = {
        'population_2020': [rf'2020 census had recorded {p}', rf'{p} in 2020', rf'up from {p} at the 2020 census'],
        'population_2025': [rf'2025 census recorded {p}', rf'{p} in 2025', rf'2025 census counted {p}'],
        'area_km2': [rf'covers {p} square kilometres', rf'over {p} square kilometres', rf'on an area of {p} square kilometres'],
        'founded_year': [rf'[Ff]ounded in {p}'],
        'budget_2025_million_tenge': [rf'budget for 2025 was set at {p} million tenge', rf"2025 budget amounted to {p} million tenge", rf'2025 budget was {p} million tenge'],
        'schools_2025_26': [rf'[Tt]here are {p} schools', rf'has {p} schools', rf'runs {p} schools'],
        'pupils_2025_26': [rf'attended by {p} pupils', rf'enrolled {p} pupils', rf'with {p} pupils enrolled'],
        'paved_road_km': [rf'paved road network is {p} km', rf'keeps {p} km of paved road', rf'maintains {p} km of paved road'],
        'water_2025_m3': [rf'water use in 2025 came to {p} (megalitres|cubic metres)', rf'[Hh]ouseholds used {p} (megalitres|cubic metres)', rf'water during 2025.*?{p} (megalitres|cubic metres)'],
    }
    if field == 'water_2025_m3':
        for pattern in patterns[field]:
            match = re.search(pattern, text, re.I)
            if match:
                value = _number(match.group(1))
                return value * (1000 if match.group(2).lower() == 'megalitres' else 1)
        raise ValueError('Field unavailable or invalid')
    return _match(patterns[field], text)


def lookup(district, field):
    try:
        if not isinstance(district, str) or not isinstance(field, str) or len(district)>80 or len(field)>80:
            return {'error':'Unsupported field or invalid arguments'}
        if field not in FIELDS:
            return {'error':'Unsupported field or invalid arguments'}
        with CORPUS.open(encoding='utf-8') as handle:
            docs = [json.loads(line) for line in handle if line.strip()]
        profiles = {d['title'].split(' district profile')[0]: d for d in docs if 'district profile (2025)' in d['title']}
        if district not in profiles:
            suggestions = difflib.get_close_matches(district, profiles.keys(), n=3, cutoff=.45)
            return {'error':f'Unknown district: {district}', 'suggestions':suggestions}
        sources = [profiles[district]['id']]
        if field == 'water_tariff_2025_tenge_per_m3':
            tariff = next(d for d in docs if d['id']=='NR-15')
            value = _match([rf'2025 the regional household water tariff is {NUMBER} tenge'], tariff['text'])
            sources = ['NR-15']
        else:
            value = _extract(profiles[district]['text'], field)
            if district == 'Brightwater' and field == 'area_km2':
                correction = next(d for d in docs if d['id']=='NR-16')
                value = _match([rf'correct area is {NUMBER} square kilometres'], correction['text'])
                sources.append('NR-16')
        if field in ('population_2020','population_2025','founded_year','schools_2025_26','pupils_2025_26'):
            value = int(value)
        return {'district':district,'field':field,'value':value,'unit':UNITS[FIELDS.index(field)],'sources':sources}
    except Exception as exc:
        return {'error':'Field unavailable or invalid', 'detail':str(exc)[:120]}


OPS = {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}
MAX_ABS = 1e15

def calculate(expression):
    if not isinstance(expression, str) or len(expression)>250:
        return {'error':'Expression exceeds safety limits'}
    try:
        tree = ast.parse(expression, mode='eval')
    except (SyntaxError, ValueError, RecursionError):
        return {'error':'Invalid arithmetic expression'}
    if sum(1 for _ in ast.walk(tree)) > 60:
        return {'error':'Expression exceeds safety limits'}
    def walk(node, depth=0):
        if depth>25:
            raise OverflowError
        if isinstance(node, ast.Expression):
            return walk(node.body, depth+1)
        if isinstance(node, ast.Constant) and type(node.value) in (int,float):
            value = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value = walk(node.operand,depth+1) * (-1 if isinstance(node.op,ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in OPS:
            value = OPS[type(node.op)](walk(node.left,depth+1),walk(node.right,depth+1))
        else:
            raise TypeError('Unsupported operator' if isinstance(node,ast.BinOp) else 'Unsupported expression')
        if not math.isfinite(value) or abs(value)>MAX_ABS:
            raise OverflowError
        return value
    try:
        return {'result':walk(tree)}
    except ZeroDivisionError:
        return {'error':'Division by zero'}
    except OverflowError:
        return {'error':'Expression exceeds safety limits'}
    except (TypeError,ValueError) as exc:
        return {'error':str(exc)}
