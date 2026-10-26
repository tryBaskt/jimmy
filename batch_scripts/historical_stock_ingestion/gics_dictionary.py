#!/usr/bin/env python3
"""Extract the current code/label dictionary from MSCI's public structure file.

The source marks legacy discontinued codes and annotates newly changed labels.
Discontinued rows are excluded from the current map and retained separately as
an incomplete historical reference, not a full historical taxonomy dictionary.
Only codes/labels are retained; business descriptions and workbook notes are dropped.
"""
import argparse
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zipfile import ZipFile

SOURCE_URL = 'https://www.msci.com/documents/1296102/23c8ec04-fd1c-3518-e04c-4aa37027889d'
NS = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def extract(data):
    with ZipFile(BytesIO(data)) as book:
        strings = [''.join(node.itertext()) for node in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si',NS)]
        root = ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
        pairs = {}
        for row in root.findall('.//s:row',NS):
            values = {}
            for cell in row.findall('s:c',NS):
                value=cell.find('s:v',NS)
                if value is not None:
                    values[re.sub(r'\d','',cell.attrib['r'])] = strings[int(value.text)] if cell.attrib.get('t')=='s' else value.text
            for code_column,label_column in [('A','B'),('C','D'),('E','F'),('G','H')]:
                code=values.get(code_column,'')
                if re.fullmatch(r'\d{2}|\d{4}|\d{6}|\d{8}',code) and values.get(label_column):
                    pairs[code]=values[label_column].strip()
    removed={code:label.replace(' (Discontinued)','') for code,label in pairs.items() if '(Discontinued)' in label}
    current={code:' '.join(re.sub(r'\s*\((?:New[^)]*|Definition Update|Sector Change[^)]*)\)\s*',' ',label,flags=re.I).replace('*','').split())
             for code,label in pairs.items() if '(Discontinued)' not in label}
    counts={size:sum(len(code)==size for code in current) for size in [2,4,6,8]}
    if counts != {2:11,4:25,6:74,8:163}:
        raise ValueError(f'Official taxonomy structure changed; inspect before accepting: {counts}')
    return {'source_url':SOURCE_URL,'effective_after_close':'2023-03-17','codes':current,'historical_discontinued_codes':removed}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--input',type=Path,help='Optional already downloaded official workbook')
    args=parser.parse_args()
    if args.input:
        data=args.input.read_bytes()
    else:
        with urlopen(Request(SOURCE_URL,headers={'User-Agent':'Jimmy GICS dictionary validation'}),timeout=60) as response:
            data=response.read()
    result=extract(data)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print('Validated 11 sectors, 25 groups, 74 industries and 163 sub-industries')

if __name__=='__main__':
    main()
