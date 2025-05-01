import tkinter as tk
from tornado.ioloop import IOLoop
from tkinter import filedialog
from bokeh.io import curdoc
from bokeh.plotting import figure
from bokeh.models import ColumnDataSource, BoxEditTool, TapTool, LabelSet, Button, CheckboxGroup, TextInput, Div, Range1d, Slider, Select, RangeSlider, LinearColorMapper
from bokeh.layouts import column, row
from bokeh.events import SelectionGeometry
from bokeh.server.server import Server
import numpy as np
import json, os, pathlib
import tifffile

updating = False
initial_shape = ()

#_______________________________________________________
def make_document(doc):

    arr_global = None

    checkbox_maxproj = CheckboxGroup(labels=["Max projection"], active=[1])
    downscale=['0','1','2','3','4','5']
    dropdown_downscale  = Select(value=downscale[0], title='Downscaling', options=downscale)


    # Initial dummy image
    initial_img = np.random.randint(0, 255, (1000, 1000), dtype=np.uint8)[::-1]

    # Image data source (for dynamic updates)
    image_source = ColumnDataSource(data=dict(
        image=[initial_img], x=[0], y=[0], dw=[initial_img.shape[1]], dh=[initial_img.shape[0]]
    ))

    images_source = ColumnDataSource(data=dict(
        image=[initial_img], x=[0], y=[0], dw=[initial_img.shape[1]], dh=[initial_img.shape[0]]
    ))

    image_max_source = ColumnDataSource(data=dict(
        image=[initial_img], x=[0], y=[0], dw=[initial_img.shape[1]], dh=[initial_img.shape[0]]
    ))


    text_input = TextInput(title="Enter image path on server:")
    status = Div(text="")

    # Figure setup
    p = figure(
        title="RoIs tracking selector",
        x_range=(0, initial_img.shape[1]), y_range=(0, initial_img.shape[0]),
        tools="pan,wheel_zoom,box_select,reset,undo,redo",
        match_aspect=True
    )
    # Display image from source
    color_mapper = LinearColorMapper(palette="Greys256", low=0, high=255)
    p.image('image', x='x', y='y', dw='dw', dh='dh', source=image_source,  color_mapper=color_mapper)

    source = ColumnDataSource(data=dict(
        x=[], y=[], width=[], height=[], index=[], label_x=[], label_y=[]
    ))

    rect_glyph = p.rect(
        'x', 'y', 'width', 'height', source=source,
        fill_alpha=0.2, fill_color='blue', line_color='red', line_width=2
    )

    box_edit = BoxEditTool(renderers=[rect_glyph], num_objects=100)
    p.add_tools(box_edit)
    p.toolbar.active_drag = box_edit

    tap = TapTool(renderers=[rect_glyph])
    p.add_tools(tap)
    p.toolbar.active_tap = tap

    labels = LabelSet(
        x='label_x', y='label_y', text='index', source=source,
        text_baseline='middle', text_align='left', text_color='white'
    )
    p.add_layout(labels)

    slider = Slider(start=0, end=0, value=0, step=1, title="z-slice", width=250)
  



    #___________________________________________________________________________________________
    def select_y_range(attr, old, new):
        if len(checkbox_maxproj.active)==2:
                image_source.data = {'image':image_max_source.data['image'], 
                            'x':image_max_source.data['x'],
                            'y':image_max_source.data['y'],
                            'dw':image_max_source.data['dw'],
                            'dh':image_max_source.data['dh']}
                slider.start=0
                slider.end=0
                slider.value=0
                
        else:
            slider.start=0
            slider.end=len(images_source.data['images'])-1
            slider.value=0
            time_point = slider.value
            image_source.data = {'image':[images_source.data['images'][time_point]], 
                                'x':[images_source.data['x'][time_point]],
                                'y':[images_source.data['y'][time_point]],
                                'dw':[images_source.data['dw'][time_point]],
                                'dh':[images_source.data['dh'][time_point]]}
    checkbox_maxproj.on_change('active', select_y_range)

    #_______________________________________________________
    def downscale_image(image, n=4, order=0, verbose=False) :
        from scipy.ndimage import zoom
        initial_shape = image.shape
        for _ in range(n) :
            if image.ndim==3:
                image = zoom(image, (1, 1/2, 1/2), order=order)
            else:
                image = zoom(image, (1/2, 1/2), order=order)

        final_shape = image.shape
        if verbose :
            print(f'Lowered resolution from {initial_shape} to {final_shape}')
        return image

    #_______________________________________________________
    def update_labels(attr, old, new):
        global updating
        if updating:
            return
        updating = True
        d = source.data
        xs, ys, ws, hs = d.get('x', []), d.get('y', []), d.get('width', []), d.get('height', [])
        idxs, lx, ly = [], [], []
        for i, (x, y, w, h) in enumerate(zip(xs, ys, ws, hs)):
            idxs.append(str(i+1))
            lx.append(x - w/2)
            ly.append(y + h/2)
        # assign full dict back to source to trigger UI update
        source.data = dict(
            x=xs, y=ys, width=ws, height=hs,
            index=idxs, label_x=lx, label_y=ly
        )
        updating = False

    dsource = source  # alias to avoid confusion
    dsource.on_change('data', update_labels)


    #_______________________________________________________
    def delete_selected():
        inds = source.selected.indices
        print('inds   ',inds)
        if not inds:
            return
        data = dict(source.data)
        for i in sorted(inds, reverse=True):
            for key in data:
                data[key].pop(i)
        source.data = data
        source.selected.indices = []

    btn_delete = Button(label="Delete Selected", button_type="danger")
    btn_delete.on_click(delete_selected)

    #_______________________________________________________
    def move_up():
        inds = source.selected.indices
        if len(inds) != 1:
            return
        i = inds[0]
        if i == 0:
            return
        data = dict(source.data)
        for key in ['x', 'y', 'width', 'height']:
            data[key][i], data[key][i-1] = data[key][i-1], data[key][i]
        source.data = data
        source.selected.indices = [i-1]

    btn_up = Button(label="Move Up")
    btn_up.on_click(move_up)

    #_______________________________________________________
    def move_down():
        inds = source.selected.indices
        if len(inds) != 1:
            return
        i = inds[0]
        if i == len(source.data['x']) - 1:
            return
        data = dict(source.data)
        for key in ['x', 'y', 'width', 'height']:
            data[key][i], data[key][i+1] = data[key][i+1], data[key][i]
        source.data = data
        source.selected.indices = [i+1]

    btn_down = Button(label="Move Down")
    btn_down.on_click(move_down)


    #_______________________________________________________
    def save_rectangles():
        global initial_shape
        global arr_global
        print('intititititititit  ', initial_shape)
        scaling = 2 ** int(dropdown_downscale.value)
        data = source.data
        out = []

        print(status.text.split("Selected: "))
        filename = status.text.split("Selected: ")[-1]
        dirname  = pathlib.Path(filename).parent.resolve()
        channel = os.path.basename(filename)
        channel = channel.replace(".tif","").split("_")[-1]
        print(dirname, '   ',channel)



        for i, (x, y, w, h) in enumerate(zip(data.get('x', []), data.get('y', []), data.get('width', []), data.get('height', []))):
            out.append({'x': x*scaling, 'y': initial_shape[0] - y*scaling, 'width': w*scaling, 'height': h*scaling, 'order': i+1})


        outdict = {'channel':channel, 'shape':arr_global.shape, 'RoIs':out}
        with open(os.path.join(dirname,"tracking_RoIs.json"), "w") as f:
            json.dump(outdict, f, indent=2)
        print("Saved tracking_RoIs.json")

    btn_save = Button(label="Save Tracking RoIs", button_type="success")
    btn_save.on_click(save_rectangles)

    #_______________________________________________________
    def select_roi_callback(event):
        if isinstance(event, SelectionGeometry):
            if event.geometry["type"]!='rect':return

            data_rect = dict(
                x= source.data['x']+[event.geometry['x0'] + (event.geometry['x1']-event.geometry['x0'])/2. ],
                y= source.data['y']+[event.geometry['y0'] + (event.geometry['y1']-event.geometry['y0'])/2.],
                width= source.data['width']+[event.geometry['x1']-event.geometry['x0']],
                height= source.data['height']+[event.geometry['y1']-event.geometry['y0']],
                index=source.data['index']+["none"],
                label_x=source.data['label_x']+[event.geometry['x0']],
                label_y=source.data['label_y']+[event.geometry['y0']]
                )

            source.data = data_rect
    p.on_event(SelectionGeometry, select_roi_callback)

    #_______________________________________________________
    def load_image(file_path):
        global arr_global
        try:
            im =  tifffile.imread(file_path)
            arr_global = np.array(im)
            source.data = {}
            slider.value = 0
            fill_source_image()
        except Exception as e:
            status.text = f"Error loading image: {e}"



    #_______________________________________________________
    def fill_source_image():
            global arr_global
            global initial_shape
            
            if arr_global.ndim == 3 :
                initial_shape = arr_global.shape[1:]
            else :
                initial_shape = arr_global.shape
            arr=downscale_image(arr_global, int(dropdown_downscale.value))
            images_dict={'images':[], 'x':[], 'y':[], 'dw':[], 'dh':[]}
            img=None
            if arr.ndim==3:
                print(arr.shape)
                img=arr[slider.value]
                slider.end = arr.shape[0]-1
                max_proj = np.max(arr, axis=0)

                for image in arr:
                    max_value = np.max(image)
                    min_value = np.min(image)
                    intensity_normalized = (image - min_value)/(max_value-min_value)*255
                    intensity_normalized = intensity_normalized.astype(np.uint8)
                    intensity_normalized = np.flip(intensity_normalized,0)
                    images_dict['images'].append(intensity_normalized)
                    images_dict['x'].append(0)
                    images_dict['y'].append(0)
                    images_dict['dw'].append(intensity_normalized.shape[1])
                    images_dict['dh'].append(intensity_normalized.shape[0])
                images_source.data = images_dict

            if arr.ndim==2:
                print(arr.shape)
                img=arr
                slider.end = 0
                max_proj = img
                max_value = np.max(max_proj)
                min_value = np.min(max_proj)
                max_proj_norm = (max_proj - min_value)/(max_value-min_value)*255
                max_proj_norm = max_proj_norm.astype(np.uint8)
                max_proj_norm = np.flip(max_proj_norm,0)
                images_source.data = dict(images=[max_proj_norm], x=[0], y=[0], dw=[max_proj_norm.shape[1]], dh=[max_proj_norm.shape[0]])


            max_value = np.max(img)
            min_value = np.min(img)
            intensity_normalized = (img - min_value)/(max_value-min_value)*255
            intensity_normalized = intensity_normalized.astype(np.uint8)
            intensity_normalized = np.flip(intensity_normalized,0)

            max_value = np.max(max_proj)
            min_value = np.min(max_proj)
            max_proj_norm = (max_proj - min_value)/(max_value-min_value)*255
            max_proj_norm = max_proj_norm.astype(np.uint8)
            max_proj_norm = np.flip(max_proj_norm,0)

            image_source.data = dict(image=[intensity_normalized], x=[0], y=[0], dw=[intensity_normalized.shape[1]], dh=[intensity_normalized.shape[0]])
            x_range = Range1d(start=0, end=intensity_normalized.shape[0])
            y_range = Range1d(start=0, end=intensity_normalized.shape[1])
            p.x_range=x_range
            p.y_range=y_range

            image_max_source.data = {'image':[max_proj_norm], 'x':[0], 'y':[0], 'dw':[max_proj_norm.shape[1]],'dh':[max_proj_norm.shape[0]]}
            images_dict = {'images':[], 'x':[],'y':[],'dw':[],'dh':[]}



            if len(checkbox_maxproj.active)==2:
                image_source.data = dict(image_max_source.data)
                slider.end=0

    #___________________________________________________________________________________________
    def update_images(attr, old, new):
        fill_source_image()
    dropdown_downscale.on_change('value', update_images)


    #___________________________________________________________________________________________
    def callback_slider(attr, old, new):
        if len(checkbox_maxproj.active)==2:return
        time_point = slider.value
        image_source.data = {'image':[images_source.data['images'][time_point]], 
                            'x':[images_source.data['x'][time_point]],
                            'y':[images_source.data['y'][time_point]],
                            'dw':[images_source.data['dw'][time_point]],
                            'dh':[images_source.data['dh'][time_point]]}
    slider.on_change('value', callback_slider)

    #_______________________________________________________
    def open_file_dialog():
        try:
            # Run in headless-safe way
            root = tk.Tk()
            root.withdraw()
            file_path = filedialog.askopenfilename(filetypes=[("Image files", "*.png;*.jpg;*.jpeg;*.tif;*.tiff")])
            root.destroy()

            if file_path:
                status.text = f"Selected: {file_path}"
                # You can call your image loading function here
                load_image(file_path)
            else:
                status.text = "No file selected."
        except Exception as e:
            status.text = f"Error: {e}"

    select_button = Button(label="Browse Image...", button_type="primary")
    select_button.on_click(open_file_dialog)


   #___________________________________________________________________________________________
    def update_contrast(attr, old, new):
        low, high = new 
        color_mapper.low = low
        color_mapper.high = high

    contrast_slider = RangeSlider(start=0, end=255, value=(0, 255), step=1, title="Contrast", width=150)
    contrast_slider.on_change('value', update_contrast)



    # Layout all widgets
    controls = row(btn_up, btn_down, btn_delete, btn_save)
    slider_layout = row(slider)
    layout = column(select_button, row(p,column(checkbox_maxproj,dropdown_downscale, contrast_slider)), slider_layout, controls, status)
    doc.add_root(layout)


 
#_______________________________________________________
def run_server():
    io_loop = IOLoop()
    server = Server({'/': make_document},
                    io_loop=io_loop,
                    allow_websocket_origin=["localhost:5006"],
                    port=5006)
    server.start()                  # bind sockets & routes
    io_loop.start()                 # enter the IOLoop





if __name__ == '__main__':
    #run_server()
    #to run a detached server
    import threading
    thread = threading.Thread(target=run_server)
    thread.start()
    print("Bokeh is now serving at http://localhost:5006/")
