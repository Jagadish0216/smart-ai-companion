"""Create and independently CRC/hash/inventory-check the shop and engineering ZIPs."""
import hashlib,json,struct,zipfile,subprocess
from pathlib import Path
from build import ROOT,OUT,PARTS

VIEWS=['front_left','front_right','side','rear','bottom','cutaway','base_internal','exploded']
PNGS=['final_'+v+'.png' for v in VIEWS]+['comparison_66mm_vs_optimized.png']
DOCS=['PRINTING.md','ASSEMBLY.md','VALIDATION.md','COST_OPTIMIZATION.md']
SHOP='Smart_Companion_Cost_Optimized'

def sha(data):return hashlib.sha256(data).hexdigest()

def write_zip(path,items):
    assert len(items)==len(set(items))
    manifest={}
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(items.items()):
            assert data,name
            z.writestr(name,data)
            manifest[name]={'bytes':len(data),'sha256':sha(data)}
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert set(z.namelist())==set(items)
        for n,r in manifest.items():
            b=z.read(n);assert len(b)==r['bytes'] and sha(b)==r['sha256'],n
    target=path.with_suffix('.manifest.json')
    target.write_text(json.dumps({'archive':path.name,'archive_sha256':sha(path.read_bytes()),'entries':manifest,'verification':'PASS: exact inventory, nonzero size, CRC and SHA256'},indent=2)+'\n')
    return {'archive':path.name,'bytes':path.stat().st_size,'file_count':len(items),'sha256':sha(path.read_bytes()),'manifest':target.name,'result':'PASS'}

def main():
    git=[]
    for command in [['git','status','--short'],['git','branch','--show-current'],['git','diff','--check'],['git','diff','--stat']]:
        result=subprocess.run(command,cwd=ROOT.parents[2],capture_output=True,text=True)
        assert result.returncode==0,(command,result.stderr)
        git.append({'command':' '.join(command),'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
    assert git[1]['stdout'].strip()=='enclosure-v1'
    (OUT/'git_final.txt').write_text('\n'.join(r['command']+'\n'+(r['stdout'] or '(no output)\n') for r in git)+'\nNO COMMIT\nNO PUSH\n')
    facts=json.loads((OUT/'cost_and_validation.json').read_text())
    assert facts['base_mm']==[174,114,62]
    for name in PNGS:
        data=(OUT/name).read_bytes()
        assert data[:8]==b'\x89PNG\r\n\x1a\n',name
        width,height=struct.unpack('>II',data[16:24])
        assert width>=1600 and height>=1200,(name,width,height)
    readme='''SMART COMPANION
FINAL COST-OPTIMIZED PRINT PACKAGE

PRINT:
All STL files in 01_STL_FILES/ (one of each for the quote).
Camera blank and lens inserts are alternatives in assembly.

FINAL DIMENSIONS:
Head: 138 × 112 × 68 mm
Base: 174 × 114 × 62 mm
Base top: 160 × 102 mm
Head tilt: 10°
Existing S.R.C. 4-ohm speaker: 78 × 78 × 40 mm reserved, cone facing down.

PRINT SETTINGS FOR QUOTE:
FDM
PLA / PLA+
0.4 mm nozzle
0.20 mm layer
3 walls/perimeters
10% infill (gyroid or grid)
Approximately 4 top/bottom layers
100% scale
millimetres
supports only where required (see PRINTING.md)

COLOURS:
WHITE / OFF-WHITE:
head shell, rear cover, base shell, base bottom cover, camera blank if used
BLACK:
front bezel, button actuator preferred, camera lens insert preferred
INTERNAL HIDDEN PARTS:
any inexpensive available colour

IMPORTANT MESSAGE TO SHOP:
"Please slice all parts before printing and send:
1. total model filament grams,
2. support grams,
3. total estimated print time,
4. material,
5. total final price.

Do not start printing until the quote is approved."

No authoritative slicer estimate was available.
CAD collision/mesh checks pass; physical fit, thermal, flex/rattle and tipping
tests remain necessary. Read VALIDATION.md and ASSEMBLY.md before assembly.
This is Option B; quote the preserved 66 mm Option A with identical settings.
Do not mix the three base parts between options. Nine head parts are unchanged.
'''
    (OUT/'READ_ME_FIRST.txt').write_text(readme,encoding='utf-8')
    items={SHOP+'/READ_ME_FIRST.txt':readme.encode('utf-8')}
    for n in PARTS:items[SHOP+'/01_STL_FILES/'+n+'.stl']=(OUT/(n+'.stl')).read_bytes()
    for n in PNGS:items[SHOP+'/02_PREVIEW_IMAGES/'+n]=(OUT/n).read_bytes()
    for n in DOCS:items[SHOP+'/03_INSTRUCTIONS/'+n]=(ROOT/n).read_bytes()
    assert len(items)==26
    assert not any('fit_test' in n.lower() or 'retainer_bar' in n.lower() or n.endswith(('.scad','.py','.log')) for n in items)
    shop=write_zip(OUT/'Smart_Companion_Cost_Optimized_Print_Package.zip',items)

    # Reproduction retains the original root/output + optimized/output relationship.
    full={}
    top='Smart_Companion_Cost_Optimized_Full_Project/'
    sources=['dimensions.scad','utilities.scad','head_shell.scad','head_rear_cover.scad','front_bezel.scad','camera_insert.scad','head_adapters.scad','base_shell.scad','base_bottom_cover.scad','speaker_mount.scad','companion_enclosure.scad','components.scad','clearance_checks.scad','ventilation_area.scad','presentation.scad','projections.scad','comparison.scad','build.py','check_clearances.py','measure_ventilation.py','render_final.py','render_comparison.py','engineering_report.py','package_optimized.py','README.md','WIRING.md','BOM.md']+DOCS
    for n in sources:full[top+'optimized/'+n]=(ROOT/n).read_bytes()
    for p in OUT.iterdir():
        if p.is_file() and p.stat().st_size>0 and p.suffix in {'.stl','.png','.json','.log','.echo','.svg','.txt'} and 'probe_' not in p.name and p.name!='package_integrity.json' and not p.name.endswith('.manifest.json'):
            full[top+'optimized/output/'+p.name]=p.read_bytes()
    baseline=ROOT.parent/'output'
    for n in PARTS:full[top+'output/'+n+'.stl']=(baseline/(n+'.stl')).read_bytes()
    for n in ['requirements_validation.json','final_dimensions_and_stability.json','ventilation_current.json']:
        full[top+'output/'+n]=(baseline/n).read_bytes()
    for n in ['Smart_Companion_66mm_Original_Backup.zip','Smart_Companion_66mm_Original_SHA256.txt']:
        full[top+'output/archive/'+n]=(baseline/'archive'/n).read_bytes()
    full[top+'README.txt']=b'Engineering archive: work in optimized/ and follow README.md. Parent output/ provides frozen Option A references. The shop-only ZIP is distributed separately. No commit. No push.\n'
    engineering=write_zip(OUT/'Smart_Companion_Cost_Optimized_Full_Project.zip',full)
    # Verify every original file captured before geometry edits is still untouched.
    with zipfile.ZipFile(baseline/'archive/Smart_Companion_66mm_Original_Backup.zip') as z:
        for n in z.namelist():assert (ROOT.parent/n).read_bytes()==z.read(n),n
    report={'shop':shop,'full_project':engineering,'shop_production_STLs':12,'shop_preview_PNGs':9,'shop_instruction_files':4,'all_original_archive_members_unchanged':True,'frozen_head_STLs_byte_identical':9,'zero_byte_files':0,'forbidden_shop_files':0,'git':git}
    (OUT/'package_integrity.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
