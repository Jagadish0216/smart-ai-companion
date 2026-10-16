// Small physical fit coupons, using the actual exported production surfaces.
// Regenerate final STLs before building these. No independent interface dimensions.
include <dimensions.scad>
use <utilities.scad>
use <head_adapters.scad>
TEST = is_undef(TEST) ? "TFT_FIT_TEST" : TEST;
FIT_MESH_PREVIEW = is_undef(FIT_MESH_PREVIEW) ? false : FIT_MESH_PREVIEW;
test_names = ["TFT_FIT_TEST","PI_USB_ETHERNET_PORT_TEST","PI_POWER_HDMI_PORT_TEST",
              "BUTTON_TACTILE_TEST","CAMERA_INSERT_FIT_TEST","COVER_CLEARANCE_TEST","SPEAKER_FIT_TEST","MICROPHONE_FIT_TEST"];
module head_mesh() { import("output/head_shell.stl"); }
module rear_mesh() { translate([0,0,head_d]) rotate([180,0,0]) import("output/head_rear_cover.stl"); }
module base_mesh() { translate([0,0,base_h]) rotate([180,0,0]) import("output/base_shell.stl"); }
module bottom_mesh() { import("output/base_bottom_cover.stl"); }
module crop(lo,hi) { translate(lo) cube(hi-lo); }
function pi_xy(p) = [pi_origin[0]-p[1],pi_origin[1]+p[0]];
mount_land = pi_standoff_d+2*wall;

module tft_test() {
    // Fuse a crop of the seated bezel and shallow head front into one quick coupon.
    // Preserves the exact visible face plane, aperture, full boss heights and pilots.
    translate([0,-tft_y,-bezel_seated_z]) intersection() {
        union() {
            head_mesh();
            translate([0,0,bezel_seated_z]) import("output/front_bezel.stl");
        }
        crop([-tft_pcb[0]/2-wall,tft_y-tft_pcb[1]/2-wall,0],
             [tft_pcb[0]/2+wall,tft_y+tft_pcb[1]/2+wall,tft_pcb_z]);
    }
}
module pi_mount_patches(points) {
    for(p=points) intersection() {
        bottom_mesh();
        translate([pi_xy(p)[0]-mount_land/2,pi_xy(p)[1]-mount_land/2,0])
            cube([mount_land,mount_land,pi_origin[2]+eps]);
    }
}
module rear_port_test() {
    left = rear_port_x[0]-fit_test_margin;
    right = min(base_w/2,rear_port_x[1]+fit_test_margin);
    height = rear_port_top+fit_test_margin;
    points = [for(p=pi_holes) if(p[0]==max([for(q=pi_holes) q[0]])) p];
    // Outside rear face down. Real rounded corner retained at right, not flattened.
    translate([-(left+right)/2,-height/2,base_d/2]) rotate([-90,0,0]) union() {
        intersection() {
            union() { base_mesh(); bottom_mesh(); }
            crop([left,base_d/2-2*wall-fit_test_margin,0],[right,base_d/2,height]);
        }
        pi_mount_patches(points);
        for(p=points) translate([pi_xy(p)[0]-mount_land/2,pi_xy(p)[1]-mount_land/2,0])
            cube([mount_land,base_d/2-pi_xy(p)[1]+mount_land/2,floor_t]);
    }
}
module power_port_test() {
    low = right_port_y[0]-fit_test_margin;
    high = right_port_y[1]+fit_test_margin;
    height = right_port_top+fit_test_margin;
    points = [for(p=pi_holes) if(p[1]==min([for(q=pi_holes) q[1]])) p];
    // Outside right face down; two original Pi mounts fix the 9 mm plug recess.
    translate([-height/2,-(low+high)/2,base_w/2]) rotate([0,90,0]) union() {
        intersection() {
            union() { base_mesh(); bottom_mesh(); }
            crop([base_w/2-2*wall-fit_test_margin,low,0],[base_w/2,high,height]);
        }
        pi_mount_patches(points);
        for(p=points) translate([pi_xy(p)[0]-mount_land/2,pi_xy(p)[1]-mount_land/2,0])
            cube([base_w/2-pi_xy(p)[0]+mount_land/2,mount_land,floor_t]);
    }
}
module button_test() {
    half = button_flange_d/2+wall;
    translate([-button_z,-button_y,head_w/2]) rotate([0,90,0]) intersection() {
        head_mesh();
        crop([head_w/2-wall,button_y-half,button_z-half],[head_w/2,button_y+half,button_z+half]);
    }
}
module camera_test() {
    half_x = camera_mount_x+camera_retainer_d/2+wall;
    half_y = camera_flange_d/2+wall;
    translate([0,-camera_y,0]) intersection() {
        head_mesh();
        crop([-half_x,camera_y-half_y,0],[half_x,camera_y+half_y,camera_insert_seat]);
    }
    // Two loose, exact inserts, not connected by sacrificial sprues.
    for(i=[0,1]) translate([(2*i-1)*(camera_mount_x+camera_retainer_d/2+fit_test_gap/2),
                          half_y+fit_test_gap+camera_flange_d/2,0])
        import(i==0 ? "output/camera_blank_insert.stl" : "output/camera_lens_insert.stl");
}
module cover_test() {
    size = 2*head_cover_boss_d+2*wall;
    depth = m3_thread_depth+lip_h+wall;
    mid = [head_w/2-size/2,head_h/2-size/2];
    translate([-(size+fit_test_gap)/2-mid[0],-mid[1],-(head_rear_z-depth)]) intersection() {
        head_mesh();
        crop([head_w/2-size,head_h/2-size,head_rear_z-depth],[head_w/2,head_h/2,head_rear_z]);
    }
    translate([(size+fit_test_gap)/2-mid[0],mid[1],head_d]) rotate([180,0,0]) intersection() {
        rear_mesh();
        crop([head_w/2-size,head_h/2-size,head_rear_z-lip_h],[head_w/2,head_h/2,head_d]);
    }
}
module fit_piece(name) {
    if(FIT_MESH_PREVIEW) import(str("output/fit_tests/",name,".stl"));
    else if(name=="TFT_FIT_TEST") tft_test();
    else if(name=="PI_USB_ETHERNET_PORT_TEST") rear_port_test();
    else if(name=="PI_POWER_HDMI_PORT_TEST") power_port_test();
    else if(name=="BUTTON_12MM_TEST") button_test();
    else if(name=="BUTTON_TACTILE_TEST") {
        button_test();
        translate([32,0,0]) button_actuator_print();
        translate([-32,0,0]) button_holder_print();
    }
    else if(name=="MICROPHONE_FIT_TEST") microphone_holder_print();
    else if(name=="SPEAKER_FIT_TEST") {
        import("output/universal_speaker_mount.stl");
        // 79 mm internal frame gauge: 0.5 mm per side on a 78 mm speaker.
        // Gauge tops are exactly 40 mm above the 2.5 mm gasket seating datum.
        for(x=[-1,1],y=[-1,1]) scale([x,y,1]) {
            translate([39.5,30,2.9]) cube([3,12.5,42.6]);
            translate([30,39.5,2.9]) cube([12.5,3,42.6]);
        }
    }
    else if(name=="CAMERA_INSERT_FIT_TEST") camera_test();
    else if(name=="COVER_CLEARANCE_TEST") cover_test();
    else assert(false,"Unknown TEST");
}
if(TEST=="layout") for(i=[0:len(test_names)-1])
    translate([(i%3-1)*(head_w+fit_test_gap),floor(i/3)*(head_h+fit_test_gap),0])
        color(i%2==0 ? col_shell : [0.25,0.42,0.50]) fit_piece(test_names[i]);
else fit_piece(TEST);
