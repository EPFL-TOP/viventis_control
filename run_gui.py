import PySimpleGUI as sg
import sys, os
import time
import threading
from datetime import datetime, timezone
import numpy as np
import tifffile
import random
import json
import math

base_outdir = r'D:\Data\Temp'

sys.path.insert(0, r"C:\Viventis\PyMCS\v2.0.0.0")
import pymcs

microscope = pymcs.Microscope()
microscope.connect()
time_lapse_controller  = pymcs.TimeLapseController(microscope)
acquisition_controller = pymcs.AcquisitionController(microscope, "ACQ")

camera    = pymcs.Camera(microscope, "CAM")
stage_xyz = pymcs.StageXYZ(microscope, "STAGE")

AppFont = 'Helvetica 12'
TabFont = 'Helvetica 14'
sg.theme('DarkTeal12')

point_count    = 10
pulse_count    = 10
position_name  = "Ablation"
point_distance = 1
cut_direction  = "X"

cut_type = "Line"
circle_diam = 10

camera_view         = 1
camera_channel      = 1
camera_plane        = 1
camera_pixel_left   = -1
camera_pixel_top    = -1
camera_pixel_width  = -1
camera_pixel_height = -1

experiment_name = 'test'

loop_running = False
do_laser = False

time_lapse  = []
metadata    = []
time_stamps = []

def acquisition_loop():
    global loop_running
    global do_laser
    while loop_running:
        time_lapse_controller.snap()
        timestamp = datetime.now(timezone.utc).isoformat()
        time_stamps.append(timestamp)
        image = None
        if camera_pixel_left>0 and camera_pixel_top>0 and camera_pixel_width>0 and camera_pixel_height>0:
            image = camera.image_get(camera_view, camera_channel, camera_plane, camera_pixel_left, camera_pixel_top, camera_pixel_width, camera_pixel_height)
        else:
            image = camera.image_get(camera_view, camera_channel, camera_plane)
        
        metadata.append(camera.image_info_get())
        time_lapse.append(image)

        print(camera.image_info_get())
    if not loop_running and do_laser:
        print('laser cut')
        do_laser = False

        if cut_type == "Line":
            start_offset = -1 * (point_count - 1) * point_distance / 2        
            for i in range(point_count):
                if cut_direction == 'X':
                    stage_xyz.move(position_name, None, None, (start_offset + i * point_distance, 0, 0))
                else:
                    stage_xyz.move(position_name, None, None, (0, start_offset + i * point_distance, 0))
                acquisition_controller.laser_ablate_uv(pulse_count)
        

        if cut_type == "Circle":
            for i in range(point_count):
                angle = i*2*math.pi/point_count
                x = circle_diam*math.cos(angle)
                y = circle_diam*math.sin(angle)
                stage_xyz.move(position_name, None, None, (x, y, 0))
                acquisition_controller.laser_ablate_uv(pulse_count)
                print("x, y ",x," ",y)
        stage_xyz.move(position_name)

        loop_running = True
        threading.Thread(target=acquisition_loop, daemon=True).start()


layout = [
    [sg.Text("Pulse count", font=AppFont),                                      sg.Text("Point count", font=AppFont),                                      sg.Text("Point distance", font=AppFont)],
    [sg.Input(key="PULSE_COUNT", size=(9, 1), font=AppFont, default_text='10'), sg.Input(key="POINT_COUNT", size=(9, 1), font=AppFont, default_text='10'), sg.Input(key="POINT_DISTANCE", size=(9, 1), font=AppFont, default_text='1')],

    [sg.HorizontalSeparator(color='red')],
    [sg.Text("Cut Type   ", font=AppFont)],
    [sg.Combo(["Line", "Circle"], key="CUT_TYPE", size=(10, 1), default_value="Line", font=AppFont)],
    [sg.HorizontalSeparator(color='red')],
    [sg.Input(key="CIRCLE_DIAM", size=(5, 1), font=AppFont, default_text='10')],

    [sg.Text("Cut direction   ", font=AppFont), sg.Text("Position name", font=AppFont)],
    [sg.Combo(["X", "Y"], key="CUT_DIR", size=(10, 1), default_value="X", font=AppFont), sg.Input(key="POSITION_NAME", size=(18, 1), font=AppFont, default_text='Ablation')],
 
    [sg.HorizontalSeparator(color='red')],

    [sg.Text("View", font=AppFont),                                            sg.Text("Channel", font=AppFont),                                            sg.Text("Plane", font=AppFont)],
    [sg.Input(key="CAMERA_VIEW", size=(5, 1), font=AppFont, default_text='1'), sg.Input(key="CAMERA_CHANNEL", size=(5, 1), font=AppFont, default_text='1'), sg.Input(key="CAMERA_PLANE", size=(5, 1), font=AppFont, default_text='1')],

    [sg.Text("left      ", font=AppFont),                                           sg.Text("top     ", font=AppFont),                                             sg.Text("width  ", font=AppFont),                                            sg.Text("height", font=AppFont)],
    [sg.Input(key="PIXEL_LEFT", size=(5, 1), font=AppFont, default_text='-1'), sg.Input(key="PIXEL_TOP", size=(5, 1), font=AppFont, default_text='-1'), sg.Input(key="PIXEL_WIDTH", size=(5, 1), font=AppFont, default_text='-1'), sg.Input(key="PIXEL_HEIGHT", size=(5, 1), font=AppFont, default_text='-1')],

    [sg.HorizontalSeparator(color='red')],
    [sg.Text("Experiment Name", font=AppFont)],
    [sg.Input(key="EXP_NAME", size=(20, 1), font=AppFont, default_text='test')],
    [sg.HorizontalSeparator(color='red')],

    [sg.Button("Set Parameters", font=AppFont)],
    [sg.Button("Start Acquisition", font=AppFont), sg.Button("Stop Acquisition", font=AppFont)],
    [sg.Button("Start Laser", font=AppFont)],

]

window = sg.Window("Viventis Ablation GUI", layout, size=(400,750))

while True:
    event, values = window.read()
    #print('event  ',event)
    #print('values ',values)
    LASER=False
    if event == sg.WINDOW_CLOSED:
        break

    if event == "Set Parameters":
        camera_view = values["CAMERA_VIEW"]
        if camera_view.isdigit():
            camera_view = int(camera_view)
        else:
            sg.popup("Please enter a valid integer for camera_view.")

        camera_channel = values["CAMERA_CHANNEL"]
        if camera_channel.isdigit():
            camera_channel = int(camera_channel)
        else:
            sg.popup("Please enter a valid integer for camera_channel.")

        camera_plane = values["CAMERA_PLANE"]
        if camera_plane.isdigit():
            camera_plane = int(camera_plane)
        else:
            sg.popup("Please enter a valid integer for camera_plane.")

        camera_pixel_left = values["PIXEL_LEFT"]
        if camera_pixel_left.lstrip('-').isdigit():
            camera_pixel_left = int(camera_pixel_left)
        else:
            sg.popup("Please enter a valid integer for camera_pixel_left.")

        camera_pixel_top = values["PIXEL_TOP"]
        if camera_pixel_top.lstrip('-').isdigit():
            camera_pixel_top = int(camera_pixel_top)
        else:
            sg.popup("Please enter a valid integer for camera_pixel_top.")

        camera_pixel_width = values["PIXEL_WIDTH"]
        if camera_pixel_width.lstrip('-').isdigit():
            camera_pixel_width = int(camera_pixel_width)
        else:
            sg.popup("Please enter a valid integer for camera_pixel_width.")

        camera_pixel_height = values["PIXEL_HEIGHT"]
        if camera_pixel_height.lstrip('-').isdigit():
            camera_pixel_height = int(camera_pixel_height)
        else:
            sg.popup("Please enter a valid integer for camera_pixel_height.")
            
        print('--------------------------------------------------')
        print('setting camera values:')
        print('camera_view    : ',camera_view)
        print('camera_plane   : ',camera_plane)
        print('camera_channel : ',camera_channel)
        print('pixel_left     : ',camera_pixel_left)
        print('pixel_top      : ',camera_pixel_top)
        print('pixel_width    : ',camera_pixel_width)
        print('pixel_height   : ',camera_pixel_height)

        pulse_count = values["PULSE_COUNT"]
        if pulse_count.isdigit():
            pulse_count = int(pulse_count)
        else:
            sg.popup("Please enter a valid integer for pulse_count.")

        point_count = values["POINT_COUNT"]
        if point_count.isdigit():
            point_count = int(point_count)
        else:
            sg.popup("Please enter a valid integer for point_count.")
    
        point_distance = values["POINT_DISTANCE"]
        if point_distance.isdigit():
            point_distance = int(point_distance)
        else:
            sg.popup("Please enter a valid integer for point_distance.")

        position_name = values["POSITION_NAME"]
        if isinstance(position_name, str):
            position_name = str(position_name)
        else:
            sg.popup("Please enter a valid string for position_name.")

        cut_direction = values["CUT_DIR"]

        print('--------------------------------------------------')
        print('setting laser values:')
        print('pulse_count    : ',pulse_count)
        print('point_count    : ',point_count)
        print('point_distance : ',point_distance)
        print('position_name  : ',position_name)
        print('cut_direction  : ',cut_direction)

        experiment_name = values["EXP_NAME"]
        if isinstance(experiment_name, str):
            experiment_name = str(experiment_name)
        else:
            sg.popup("Please enter a valid string for experiment_name.")

        print('--------------------------------------------------')
        print('setting experiment name:')
        print('experiment_name    : ',experiment_name)

        cut_type = values["CUT_TYPE"]
        circle_diam = int(values["CIRCLE_DIAM"])

        print('cut_type     ',cut_type)
        print('circle_diam  ',circle_diam)

    if event == "Start Laser":
        print('start laser button')
        loop_running = False
        do_laser = True

    if event == "Start Acquisition":
        if not loop_running:  
            time_lapse  = []
            metadata    = []
            time_stamps = []
            loop_running = True
            threading.Thread(target=acquisition_loop, daemon=True).start()
        else:
            sg.popup("The loop is already running!")

    if event == "Stop Acquisition":
        loop_running = False
        time.sleep(5)

        now = datetime.now()
        current_date = now.date()
        #current_date = current_date.replace('-','')
        random_number = random.randint(0, 999999)
        random_string = f"{random_number:06}"  
        
        outdir = os.path.join(base_outdir,'{}_{}_{}'.format(current_date,random_string, experiment_name))
        print('--------------',outdir)
        os.makedirs(outdir)

        images_array = np.array(time_lapse)
        tifffile.imwrite(os.path.join(outdir,'output.tif'), images_array)

        out_json = {'metadata':metadata, 'time_stamps':time_stamps}
        out_file = open(os.path.join(outdir,'output.json'), "w") 
        json.dump(out_json, out_file)
        print(time_stamps)
        out_file.close()


window.close()

