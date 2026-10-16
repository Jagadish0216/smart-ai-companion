"""Export eight physical coupons from the production enclosure STLs; no dependencies."""
import argparse
import hashlib
import json
import zipfile
from build import ROOT, OUT, PARTS, run, mesh_report

FIT_OUT=OUT/'fit_tests'
TESTS={
    'TFT_FIT_TEST':1,
    'PI_USB_ETHERNET_PORT_TEST':1,
    'PI_POWER_HDMI_PORT_TEST':1,
    'BUTTON_TACTILE_TEST':3,
    'CAMERA_INSERT_FIT_TEST':3,
    'COVER_CLEARANCE_TEST':2,
    'SPEAKER_FIT_TEST':1,
    'MICROPHONE_FIT_TEST':1,
}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--openscad',default=r'C:\Program Files\OpenSCAD\openscad.exe')
    ap.add_argument('--only',nargs='+',choices=list(TESTS))
    args=ap.parse_args()
    FIT_OUT.mkdir(exist_ok=True)
    run(args.openscad,'fit_tests/version',['--version'])
    reports=[]
    for name,shells in TESTS.items():
        target=FIT_OUT/(name+'.stl')
        if not args.only or name in args.only:
            run(args.openscad,'fit_tests/'+name,['--export-format','binstl','-o',target,
                '-D',f'TEST="{name}"',ROOT/'fit_tests.scad'])
        reports.append(mesh_report(target,expected_shells=shells))
    (FIT_OUT/'mesh_validation.json').write_text(json.dumps(reports,indent=2)+'\n',encoding='utf-8')
    sources=[ROOT/'dimensions.scad',ROOT/'fit_tests.scad']+[OUT/(n+'.stl') for n in PARTS]
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    (FIT_OUT/'source_hashes.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
    run(args.openscad,'fit_tests/fit_tests_preview',['-o',FIT_OUT/'fit_tests_preview.png',
        '--imgsize=1800,1200','--viewall','--autocenter','--projection=o',
        '--colorscheme=Tomorrow','--camera=0,0,0,28,0,0,600',
        '-D','TEST="layout"','-D','FIT_MESH_PREVIEW=true',ROOT/'fit_tests.scad'])
    print('PASS: eight coupons; camera/button have three pieces, cover has two; others one.',flush=True)
    files=sorted([FIT_OUT/(n+'.stl') for n in TESTS]+[FIT_OUT/(n+'.log') for n in TESTS]+
                 [FIT_OUT/n for n in ['README.md','fit_tests_preview.png','mesh_validation.json','source_hashes.json','version.log']])
    manifest=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in files)
    (FIT_OUT/'SHA256SUMS.txt').write_text(manifest,encoding='utf-8')
    archive=FIT_OUT/'fit-tests-package.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files+[FIT_OUT/'SHA256SUMS.txt']:
            z.write(p,p.name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    print(f'Fit package ZIP integrity OK: {archive}',flush=True)

if __name__=='__main__':
    main()
