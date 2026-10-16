"""Measure actual projected through-openings; OpenSCAD SVG polygons, standard library."""
import argparse
import json
import re
import xml.etree.ElementTree as ET
from build import ROOT, OUT, run

def svg_area(path):
    total = 0.0
    contours = 0
    for element in ET.parse(path).iter():
        if element.tag.split('}')[-1] != 'path':
            continue
        data = element.attrib['d']
        if re.search(r'[A-DF-KN-Yb-df-kn-y]', data):
            raise ValueError('Expected OpenSCAD straight M/L/z polygon paths')
        for section in re.split(r'[zZ]',data):
            if not section.strip():
                continue
            values = [float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?', section)]
            if len(values)%2:
                raise ValueError('Malformed polygon')
            points = list(zip(values[::2],values[1::2]))
            if len(points)<3:
                raise ValueError('Degenerate polygon')
            total += sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))/2
            contours += 1
    return {'through_area_mm2':round(abs(total),3),'polygon_contours':contours}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--openscad',default=r'C:\Program Files\OpenSCAD\openscad.exe')
    ap.add_argument('--label',default='current',choices=['before','current'])
    args=ap.parse_args()
    result={}
    for area in ['intake','exhaust']:
        name=f'ventilation_{args.label}_{area}'
        svg=OUT/(name+'.svg')
        run(args.openscad,name,['-o',svg,'-D',f'AREA="{area}"',ROOT/'ventilation_area.scad'])
        result[area]=svg_area(svg)
    result['method']='Projected normal through-open area of exported meshes; includes capsule ends and blocked mounting lands; excludes connector openings.'
    (OUT/f'ventilation_{args.label}.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':
    main()
