# fmt: off
# This file is part of stable-diffusion-webui (https://github.com/sd-webui/stable-diffusion-webui/).

# Copyright 2022 sd-webui team.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# base webui import and utils.
#from webui_streamlit import st
import hydralit as st


# streamlit imports
from streamlit import StopException, StreamlitAPIException

# streamlit components section
from streamlit_server_state import server_state, server_state_lock
import hydralit_components as hc

# other imports

import warnings
import json

import base64
import os, sys, re, random, datetime, time, math, glob, toml
import gc
from PIL import Image, ImageFont, ImageDraw, ImageFilter
from PIL.PngImagePlugin import PngInfo
from scipy import integrate
import torch
from torchdiffeq import odeint
import k_diffusion as K
import math, requests
import mimetypes
import numpy as np
from numpy import asarray
import pynvml
import threading
import torch, torchvision
from torch import autocast
from torchvision import transforms
import torch.nn as nn
from omegaconf import OmegaConf
import yaml
from pathlib import Path
from contextlib import nullcontext, ExitStack
from einops import rearrange, repeat
from einops import rearrange
from ldm.util import instantiate_from_config
from retry import retry
from slugify import slugify
import skimage
import piexif
import piexif.helper
from tqdm import trange
import util.ModelRepo as ModelRepo
import util.Models as Models
from typing import Dict, List, Tuple
from ldm.models.diffusion.ddim import DDIMSampler
from ldm.util import ismap


# Temp imports 
#from basicsr.utils.registry import ARCH_REGISTRY



# end of imports
# ---------------------------------------------------------------------------------------------------------------

try:
    # this silences the annoying "Some weights of the model checkpoint were not used when initializing..." message at start.
    from transformers import logging

    logging.set_verbosity_error()
except:
    pass

# remove some annoying deprecation warnings that show every now and then.
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# this is a fix for Windows users. Without it, javascript files will be served with text/html content-type and the bowser will not show any UI
mimetypes.init()
mimetypes.add_type('application/javascript', '.js')

# some of those options should not be changed at all because they would break the model, so I removed them from options.
opt_C = 4
opt_f = 8

if not "defaults" in st.session_state:
    st.session_state["defaults"] = {}

st.session_state["defaults"] = OmegaConf.load("configs/webui/webui_streamlit.yaml")

if (os.path.exists("configs/webui/userconfig_streamlit.yaml")):
    user_defaults = OmegaConf.load("configs/webui/userconfig_streamlit.yaml")
    st.session_state["defaults"] = OmegaConf.merge(st.session_state["defaults"], user_defaults)
else:
    OmegaConf.save(config=st.session_state.defaults, f="configs/webui/userconfig_streamlit.yaml")
    loaded = OmegaConf.load("configs/webui/userconfig_streamlit.yaml")
    assert st.session_state.defaults == loaded

if (os.path.exists(".streamlit/config.toml")):
    st.session_state["streamlit_config"] = toml.load(".streamlit/config.toml")

if st.session_state["defaults"].daisi_app.running_on_daisi_io:
    if os.path.exists("scripts/modeldownload.py"):
        import modeldownload
        modeldownload.updateModels()

#
# app = st.HydraApp(title='Stable Diffusion WebUI', favicon="", sidebar_state="expanded",
                  # hide_streamlit_markers=False, allow_url_nav=True , clear_cross_app_sessions=False)


# should and will be moved to a settings menu in the UI at some point
grid_format = [s.lower() for s in st.session_state["defaults"].general.grid_format.split(':')]
grid_lossless = False
grid_quality = 100
if grid_format[0] == 'png':
    grid_ext = 'png'
    grid_format = 'png'
elif grid_format[0] in ['jpg', 'jpeg']:
    grid_quality = int(grid_format[1]) if len(grid_format) > 1 else 100
    grid_ext = 'jpg'
    grid_format = 'jpeg'
elif grid_format[0] == 'webp':
    grid_quality = int(grid_format[1]) if len(grid_format) > 1 else 100
    grid_ext = 'webp'
    grid_format = 'webp'
    if grid_quality < 0: # e.g. webp:-100 for lossless mode
        grid_lossless = True
        grid_quality = abs(grid_quality)

# should and will be moved to a settings menu in the UI at some point
save_format = [s.lower() for s in st.session_state["defaults"].general.save_format.split(':')]
save_lossless = False
save_quality = 100
if save_format[0] == 'png':
    save_ext = 'png'
    save_format = 'png'
elif save_format[0] in ['jpg', 'jpeg']:
    save_quality = int(save_format[1]) if len(save_format) > 1 else 100
    save_ext = 'jpg'
    save_format = 'jpeg'
elif save_format[0] == 'webp':
    save_quality = int(save_format[1]) if len(save_format) > 1 else 100
    save_ext = 'webp'
    save_format = 'webp'
    if save_quality < 0: # e.g. webp:-100 for lossless mode
        save_lossless = True
        save_quality = abs(save_quality)

# this should force GFPGAN and RealESRGAN onto the selected gpu as well
os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"   # see issue #152
os.environ["CUDA_VISIBLE_DEVICES"] = str(st.session_state["defaults"].general.gpu)


#

# functions to load css locally OR remotely starts here. Options exist for future flexibility. Called as st.markdown with unsafe_allow_html as css injection
# TODO, maybe look into async loading the file especially for remote fetching
def local_css(file_name):
    with open(file_name) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

def remote_css(url):
    st.markdown(f'<link href="{url}" rel="stylesheet">', unsafe_allow_html=True)

def load_css(isLocal, nameOrURL):
    if(isLocal):
        local_css(nameOrURL)
    else:
        remote_css(nameOrURL)

def set_page_title(title):
    """
    Simple function to allows us to change the title dynamically.
    Normally you can use `st.set_page_config` to change the title but it can only be used once per app.
    """

    st.sidebar.markdown(unsafe_allow_html=True, body=f"""
                            <iframe height=0 srcdoc="<script>
                            const title = window.parent.document.querySelector('title') \

                            const oldObserver = window.parent.titleObserver
                            if (oldObserver) {{
                            oldObserver.disconnect()
                            }} \

                            const newObserver = new MutationObserver(function(mutations) {{
                            const target = mutations[0].target
                            if (target.text !== '{title}') {{
                            target.text = '{title}'
                            }}
                            }}) \

                            newObserver.observe(title, {{ childList: true }})
                            window.parent.titleObserver = newObserver \

                            title.text = '{title}'
                            </script>" />
                            """)

def human_readable_size(size, decimal_places=3):
    """Return a human readable size from bytes."""
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024.0:
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f}{unit}"


class ModelNames:
    RealESRGANx4Plus = "RealESRGAN_x4plus"
    RealESRGANx4Plus_Anime_6B = "RealESRGAN_x4plus_anime_6B"
    RealESRGANx2Plus = "RealESRGAN_x4plus"  # Uses x4 model
    RealESRGANx2Plus_Anime_6B = "RealESRGAN_x4plus_anime_6B"  # Uses x4 model
    LDSR = "LDSR"
    BLIP = "BLIP"
    Txt2Vid = "Txt2Vid"
    SD_opt_unet = "sd_unet"
    SD_opt_cs = "sd_cs"
    SD_opt_fs = "sd_fs"
    SD_full = "sd_full"
    GFPGAN = "GFPGAN"

    # Aliases for optimized mode
    SD_unet = "sd_full" if not st.session_state.defaults.general.optimized else "sd_unet"
    SD_cs = "sd_full" if not st.session_state.defaults.general.optimized else "sd_cs"
    SD_fs = "sd_full" if not st.session_state.defaults.general.optimized else "sd_fs"
    # Alias for any RealESRGAN
    RealESRGAN = "RealESRGAN_x4plus"

def register_models( manager: ModelRepo.Manager ):
    register_preconfigured_models(manager)
    register_custom_models(manager)
    register_custom_esgran_models(manager)
    register_custom_gfpgan_models(manager)
    register_custom_ldsr_models(manager)

    server_state["loaded_model"] = "Stable Diffusion v1.4" # TODO: Custom models?
    server_state["device"] = torch.device("cuda") # TODO: Device?

def get_device_setting( setting_name: str ) -> torch.device:
    """ Returns a torch.device for a given setting (gfpgan, esrgan, extra-models) """
    gen_settings = st.session_state["defaults"].general
    on_cpu = gen_settings.get(f"{setting_name}_cpu", False)
    if on_cpu:
        return torch.device("cpu")
    gpu_id = gen_settings.get(f"{setting_name}_gpu", 0)
    return torch.device(f"cuda:{gpu_id}")

def register_preconfigured_models( manager:ModelRepo.Manager ):
    # Pre-configured model definitions
    model_loaders: Dict[str, ModelRepo.ModelLoader] = {
        ModelNames.GFPGAN:
            Models.GFPGAN( gfpgan_dir = st.session_state["defaults"].general.GFPGAN_dir,
                           device=get_device_setting("gfpgan")),

        ModelNames.LDSR:
            Models.LDSR( ldsr_dir=st.session_state['defaults'].general.LDSR_dir ),

        ModelNames.SD_full:
            Models.SD( checkpoint=st.session_state.defaults.general.default_model_path,
                       config_yaml=st.session_state.defaults.general.default_model_config,
                       half_precision=not st.session_state['defaults'].general.no_half ),

        ModelNames.SD_opt_cs:
            Models.SD_Optimized( checkpoint=st.session_state.defaults.general.default_model_path,
                                 config_yaml=st.session_state.defaults.general.optimized_config,
                                 stage=Models.SD_Optimized.Stage.COND_STAGE,
                                 half_precision=not st.session_state['defaults'].general.no_half),

        ModelNames.SD_opt_unet:
            Models.SD_Optimized( checkpoint=st.session_state.defaults.general.default_model_path,
                                 config_yaml=st.session_state.defaults.general.optimized_config,
                                 stage=Models.SD_Optimized.Stage.UNET,
                                 half_precision=not st.session_state['defaults'].general.no_half,
                                 max_depth = 0 if st.session_state.defaults.general.optimized_turbo else 1),

        ModelNames.SD_opt_fs:
            Models.SD_Optimized( checkpoint=st.session_state.defaults.general.default_model_path,
                                 config_yaml=st.session_state.defaults.general.optimized_config,
                                 stage=Models.SD_Optimized.Stage.FIRST_STAGE,
                                 half_precision=not st.session_state['defaults'].general.no_half),
    }

    # Register every model in model_loaders above
    for name, loader in model_loaders.items():
        manager.register_model_loader(name=name, loader=loader)


def register_custom_models( manager:ModelRepo.Manager ):
    model_loaders: Dict[str, ModelRepo.ModelLoader] = {}
    # Check for custom models
    custom_models = custom_models_available()
    for name,path in custom_models.items():
        full_loader = Models.SD( checkpoint=path,
                                 config_yaml=st.session_state.defaults.general.default_model_config,
                                 half_precision=not st.session_state['defaults'].general.no_half )
        unet_loader =  Models.SD_Optimized( checkpoint=path,
                                 config_yaml=st.session_state.defaults.general.optimized_config,
                                 stage=Models.SD_Optimized.Stage.UNET,
                                 half_precision=not st.session_state['defaults'].general.no_half)
        cs_loader =  Models.SD_Optimized( checkpoint=path,
                                 config_yaml=st.session_state.defaults.general.optimized_config,
                                 stage=Models.SD_Optimized.Stage.COND_STAGE,
                                 half_precision=not st.session_state['defaults'].general.no_half)
        fs_loader =  Models.SD_Optimized( checkpoint=path,
                                 config_yaml=st.session_state.defaults.general.optimized_config,
                                 stage=Models.SD_Optimized.Stage.FIRST_STAGE,
                                 half_precision=not st.session_state['defaults'].general.no_half)

        model_loaders[f"{name}_full"] = full_loader
        model_loaders[f"{name}_unet"] = unet_loader
        model_loaders[f"{name}_cs"] = cs_loader
        model_loaders[f"{name}_fs"] = fs_loader

    # Register every model in model_loaders above
    for name, loader in model_loaders.items():
        manager.register_model_loader(name=name, loader=loader)


def register_custom_esgran_models( manager:ModelRepo.Manager ):
    model_loaders: Dict[str, ModelRepo.ModelLoader] = {}
    # Check for custom models
    custom_models = RealESRGAN_available()
    for name,path in custom_models.items():
        model_loaders[name] = Models.RealESRGAN( esrgan_dir=st.session_state["defaults"].general.RealESRGAN_dir,
                                                 model_name=name, path=path, device=get_device_setting("esrgan"))
    # Register every model in model_loaders above
    for name, loader in model_loaders.items():
        manager.register_model_loader(name=name, loader=loader)

def register_custom_gfpgan_models( manager:ModelRepo.Manager ):
    model_loaders: Dict[str, ModelRepo.ModelLoader] = {}
    # Check for custom models
    custom_models = GFPGAN_available()
    for name,path in custom_models.items():
        model_loaders[name] = Models.GFPGAN( gfpgan_dir=st.session_state["defaults"].general.GFPGAN_dir,
                                                 model_name=name, path=path, device=get_device_setting("gfpgan"))
    # Register every model in model_loaders above
    for name, loader in model_loaders.items():
        manager.register_model_loader(name=name, loader=loader)

def register_custom_ldsr_models( manager:ModelRepo.Manager ):
    model_loaders: Dict[str, ModelRepo.ModelLoader] = {}
    # Check for custom models
    custom_models = LDSR_available()
    for name,path in custom_models.items():
        model_loaders[name] = Models.LDSR( ldsr_dir=st.session_state["defaults"].general.LDSR_dir,
                                           model_name=name, model_path=path)
    # Register every model in model_loaders above
    for name, loader in model_loaders.items():
        manager.register_model_loader(name=name, loader=loader)

# TODO: Needed?
#         # trying to disable multiprocessing as it makes it so streamlit cant stop when the
#         # model is loaded in memory and you need to kill the process sometimes.
#         try:
#             server_state["model"].args.use_multiprocessing_for_evaluation = False
#         except:
#             pass

#         if st.session_state.defaults.general.enable_attention_slicing:
#             server_state["model"].enable_attention_slicing()

#         if st.session_state.defaults.general.enable_minimal_memory_usage:
#             server_state["model"].enable_minimal_memory_usage()

#         print("Model loaded.")


class MemUsageMonitor(threading.Thread):
    stop_flag = False
    max_usage = 0
    total = -1

    def __init__(self, name):
        threading.Thread.__init__(self)
        self.name = name

    def run(self):
        try:
            pynvml.nvmlInit()
        except:
            print(f"[{self.name}] Unable to initialize NVIDIA management. No memory stats. \n")
            return
        print(f"[{self.name}] Recording max memory usage...\n")
        # Missing context
        #handle = pynvml.nvmlDeviceGetHandleByIndex(st.session_state['defaults'].general.gpu)
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        self.total = pynvml.nvmlDeviceGetMemoryInfo(handle).total
        while not self.stop_flag:
            m = pynvml.nvmlDeviceGetMemoryInfo(handle)
            self.max_usage = max(self.max_usage, m.used)
            # print(self.max_usage)
            time.sleep(0.1)
        print(f"[{self.name}] Stopped recording.\n")
        pynvml.nvmlShutdown()

    def read(self):
        return self.max_usage, self.total

    def stop(self):
        self.stop_flag = True

    def read_and_stop(self):
        self.stop_flag = True
        return self.max_usage, self.total

class CFGMaskedDenoiser(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.inner_model = model

    def forward(self, x, sigma, uncond, cond, cond_scale, mask, x0, xi):
        x_in = x
        x_in = torch.cat([x_in] * 2)
        sigma_in = torch.cat([sigma] * 2)
        cond_in = torch.cat([uncond, cond])
        uncond, cond = self.inner_model(x_in, sigma_in, cond=cond_in).chunk(2)
        denoised = uncond + (cond - uncond) * cond_scale

        if mask is not None:
            assert x0 is not None
            img_orig = x0
            mask_inv = 1. - mask
            denoised = (img_orig * mask_inv) + (mask * denoised)

        return denoised

class CFGDenoiser(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.inner_model = model

    def forward(self, x, sigma, uncond, cond, cond_scale):
        x_in = torch.cat([x] * 2)
        sigma_in = torch.cat([sigma] * 2)
        cond_in = torch.cat([uncond, cond])
        uncond, cond = self.inner_model(x_in, sigma_in, cond=cond_in).chunk(2)
        return uncond + (cond - uncond) * cond_scale
def append_zero(x):
    return torch.cat([x, x.new_zeros([1])])
def append_dims(x, target_dims):
    """Appends dimensions to the end of a tensor until it has target_dims dimensions."""
    dims_to_append = target_dims - x.ndim
    if dims_to_append < 0:
        raise ValueError(f'input has {x.ndim} dims but target_dims is {target_dims}, which is less')
    return x[(...,) + (None,) * dims_to_append]
def get_sigmas_karras(n, sigma_min, sigma_max, rho=7., device='cpu'):
    """Constructs the noise schedule of Karras et al. (2022)."""
    ramp = torch.linspace(0, 1, n)
    min_inv_rho = sigma_min ** (1 / rho)
    max_inv_rho = sigma_max ** (1 / rho)
    sigmas = (max_inv_rho + ramp * (min_inv_rho - max_inv_rho)) ** rho
    return append_zero(sigmas).to(device)

#
# helper fft routines that keep ortho normalization and auto-shift before and after fft
def _fft2(data):
    if data.ndim > 2: # has channels
        out_fft = np.zeros((data.shape[0], data.shape[1], data.shape[2]), dtype=np.complex128)
        for c in range(data.shape[2]):
            c_data = data[:,:,c]
            out_fft[:,:,c] = np.fft.fft2(np.fft.fftshift(c_data),norm="ortho")
            out_fft[:,:,c] = np.fft.ifftshift(out_fft[:,:,c])
    else: # one channel
        out_fft = np.zeros((data.shape[0], data.shape[1]), dtype=np.complex128)
        out_fft[:,:] = np.fft.fft2(np.fft.fftshift(data),norm="ortho")
        out_fft[:,:] = np.fft.ifftshift(out_fft[:,:])

    return out_fft

def _ifft2(data):
    if data.ndim > 2: # has channels
        out_ifft = np.zeros((data.shape[0], data.shape[1], data.shape[2]), dtype=np.complex128)
        for c in range(data.shape[2]):
            c_data = data[:,:,c]
            out_ifft[:,:,c] = np.fft.ifft2(np.fft.fftshift(c_data),norm="ortho")
            out_ifft[:,:,c] = np.fft.ifftshift(out_ifft[:,:,c])
    else: # one channel
        out_ifft = np.zeros((data.shape[0], data.shape[1]), dtype=np.complex128)
        out_ifft[:,:] = np.fft.ifft2(np.fft.fftshift(data),norm="ortho")
        out_ifft[:,:] = np.fft.ifftshift(out_ifft[:,:])

    return out_ifft

def _get_gaussian_window(width, height, std=3.14, mode=0):

    window_scale_x = float(width / min(width, height))
    window_scale_y = float(height / min(width, height))

    window = np.zeros((width, height))
    x = (np.arange(width) / width * 2. - 1.) * window_scale_x
    for y in range(height):
        fy = (y / height * 2. - 1.) * window_scale_y
        if mode == 0:
            window[:, y] = np.exp(-(x**2+fy**2) * std)
        else:
            window[:, y] = (1/((x**2+1.) * (fy**2+1.))) ** (std/3.14) # hey wait a minute that's not gaussian

    return window

def _get_masked_window_rgb(np_mask_grey, hardness=1.):
    np_mask_rgb = np.zeros((np_mask_grey.shape[0], np_mask_grey.shape[1], 3))
    if hardness != 1.:
        hardened = np_mask_grey[:] ** hardness
    else:
        hardened = np_mask_grey[:]
    for c in range(3):
        np_mask_rgb[:,:,c] = hardened[:]
    return np_mask_rgb

def get_matched_noise(_np_src_image, np_mask_rgb, noise_q, color_variation):
    """
     Explanation:
     Getting good results in/out-painting with stable diffusion can be challenging.
     Although there are simpler effective solutions for in-painting, out-painting can be especially challenging because there is no color data
     in the masked area to help prompt the generator. Ideally, even for in-painting we'd like work effectively without that data as well.
     Provided here is my take on a potential solution to this problem.

     By taking a fourier transform of the masked src img we get a function that tells us the presence and orientation of each feature scale in the unmasked src.
     Shaping the init/seed noise for in/outpainting to the same distribution of feature scales, orientations, and positions increases output coherence
     by helping keep features aligned. This technique is applicable to any continuous generation task such as audio or video, each of which can
     be conceptualized as a series of out-painting steps where the last half of the input "frame" is erased. For multi-channel data such as color
     or stereo sound the "color tone" or histogram of the seed noise can be matched to improve quality (using scikit-image currently)
     This method is quite robust and has the added benefit of being fast independently of the size of the out-painted area.
     The effects of this method include things like helping the generator integrate the pre-existing view distance and camera angle.

     Carefully managing color and brightness with histogram matching is also essential to achieving good coherence.

     noise_q controls the exponent in the fall-off of the distribution can be any positive number, lower values means higher detail (range > 0, default 1.)
     color_variation controls how much freedom is allowed for the colors/palette of the out-painted area (range 0..1, default 0.01)
     This code is provided as is under the Unlicense (https://unlicense.org/)
     Although you have no obligation to do so, if you found this code helpful please find it in your heart to credit me [parlance-zz].

     Questions or comments can be sent to parlance@fifth-harmonic.com (https://github.com/parlance-zz/)
     This code is part of a new branch of a discord bot I am working on integrating with diffusers (https://github.com/parlance-zz/g-diffuser-bot)

    """

    global DEBUG_MODE
    global TMP_ROOT_PATH

    width = _np_src_image.shape[0]
    height = _np_src_image.shape[1]
    num_channels = _np_src_image.shape[2]

    np_src_image = _np_src_image[:] * (1. - np_mask_rgb)
    np_mask_grey = (np.sum(np_mask_rgb, axis=2)/3.)
    np_src_grey = (np.sum(np_src_image, axis=2)/3.)
    all_mask = np.ones((width, height), dtype=bool)
    img_mask = np_mask_grey > 1e-6
    ref_mask = np_mask_grey < 1e-3

    windowed_image = _np_src_image * (1.-_get_masked_window_rgb(np_mask_grey))
    windowed_image /= np.max(windowed_image)
    windowed_image += np.average(_np_src_image) * np_mask_rgb# / (1.-np.average(np_mask_rgb))  # rather than leave the masked area black, we get better results from fft by filling the average unmasked color
    # windowed_image += np.average(_np_src_image) * (np_mask_rgb * (1.- np_mask_rgb)) / (1.-np.average(np_mask_rgb)) # compensate for darkening across the mask transition area
    #_save_debug_img(windowed_image, "windowed_src_img")

    src_fft = _fft2(windowed_image) # get feature statistics from masked src img
    src_dist = np.absolute(src_fft)
    src_phase = src_fft / src_dist
    #_save_debug_img(src_dist, "windowed_src_dist")

    noise_window = _get_gaussian_window(width, height, mode=1)  # start with simple gaussian noise
    noise_rgb = np.random.random_sample((width, height, num_channels))
    noise_grey = (np.sum(noise_rgb, axis=2)/3.)
    noise_rgb *=  color_variation # the colorfulness of the starting noise is blended to greyscale with a parameter
    for c in range(num_channels):
        noise_rgb[:,:,c] += (1. - color_variation) * noise_grey

    noise_fft = _fft2(noise_rgb)
    for c in range(num_channels):
        noise_fft[:,:,c] *= noise_window
    noise_rgb = np.real(_ifft2(noise_fft))
    shaped_noise_fft = _fft2(noise_rgb)
    shaped_noise_fft[:,:,:] = np.absolute(shaped_noise_fft[:,:,:])**2 * (src_dist ** noise_q) * src_phase # perform the actual shaping

    brightness_variation = 0.#color_variation # todo: temporarily tieing brightness variation to color variation for now
    contrast_adjusted_np_src = _np_src_image[:] * (brightness_variation + 1.) - brightness_variation * 2.

    # scikit-image is used for histogram matching, very convenient!
    shaped_noise = np.real(_ifft2(shaped_noise_fft))
    shaped_noise -= np.min(shaped_noise)
    shaped_noise /= np.max(shaped_noise)
    shaped_noise[img_mask,:] = skimage.exposure.match_histograms(shaped_noise[img_mask,:]**1., contrast_adjusted_np_src[ref_mask,:], channel_axis=1)
    shaped_noise = _np_src_image[:] * (1. - np_mask_rgb) + shaped_noise * np_mask_rgb
    #_save_debug_img(shaped_noise, "shaped_noise")

    matched_noise = np.zeros((width, height, num_channels))
    matched_noise = shaped_noise[:]
    #matched_noise[all_mask,:] = skimage.exposure.match_histograms(shaped_noise[all_mask,:], _np_src_image[ref_mask,:], channel_axis=1)
    #matched_noise = _np_src_image[:] * (1. - np_mask_rgb) + matched_noise * np_mask_rgb

    #_save_debug_img(matched_noise, "matched_noise")

    """
    todo:
    color_variation doesnt have to be a single number, the overall color tone of the out-painted area could be param controlled
    """

    return np.clip(matched_noise, 0., 1.)


#
def find_noise_for_image(model, device, init_image, prompt, steps=200, cond_scale=2.0, verbose=False, normalize=False, generation_callback=None):
    image = np.array(init_image).astype(np.float32) / 255.0
    image = image[None].transpose(0, 3, 1, 2)
    image = torch.from_numpy(image)
    image = 2. * image - 1.
    image = image.to(device)
    x = model.get_first_stage_encoding(model.encode_first_stage(image))

    uncond = model.get_learned_conditioning([''])
    cond = model.get_learned_conditioning([prompt])

    s_in = x.new_ones([x.shape[0]])
    dnw = K.external.CompVisDenoiser(model)
    sigmas = dnw.get_sigmas(steps).flip(0)

    if verbose:
        print(sigmas)

    for i in trange(1, len(sigmas)):
        x_in = torch.cat([x] * 2)
        sigma_in = torch.cat([sigmas[i - 1] * s_in] * 2)
        cond_in = torch.cat([uncond, cond])

        c_out, c_in = [K.utils.append_dims(k, x_in.ndim) for k in dnw.get_scalings(sigma_in)]

        if i == 1:
            t = dnw.sigma_to_t(torch.cat([sigmas[i] * s_in] * 2))
        else:
            t = dnw.sigma_to_t(sigma_in)

        eps = model.apply_model(x_in * c_in, t, cond=cond_in)
        denoised_uncond, denoised_cond = (x_in + eps * c_out).chunk(2)

        denoised = denoised_uncond + (denoised_cond - denoised_uncond) * cond_scale

        if i == 1:
            d = (x - denoised) / (2 * sigmas[i])
        else:
            d = (x - denoised) / sigmas[i - 1]

        if generation_callback is not None:
            generation_callback(x, i)

        dt = sigmas[i] - sigmas[i - 1]
        x = x + d * dt

    return x / sigmas[-1]

#
def folder_picker(label="Select:", value="", help="", folder_button_label="Select", folder_button_help="", folder_button_key=""):
    """A folder picker that has a text_input field next to it and a button to select the folder.
    Returns the text_input field with the folder path."""
    import tkinter as tk
    from tkinter import filedialog
    import string

    # Set up tkinter
    root = tk.Tk()
    root.withdraw()

    # Make folder picker dialog appear on top of other windows
    root.wm_attributes('-topmost', 1)

    col1, col2 = st.columns([2,1], gap="small")

    with col1:
        dirname = st.empty()
    with col2:
        st.write("")
        st.write("")
        folder_picker = st.empty()

    # Folder picker button
    #st.title('Folder Picker')
    #st.write('Please select a folder:')

    # Create a label and add a random number of invisible characters
    # to it so no two buttons inside a form are the same.
    #folder_button_label = ''.join(random.choice(f"{folder_button_label}") for _ in range(5))
    folder_button_label = f"{str(folder_button_label)}{'‎' * random.randint(1, 500)}"
    clicked = folder_button_key + '‎' * random.randint(5, 500)

    # try:
    #clicked = folder_picker.button(folder_button_label, help=folder_button_help, key=folder_button_key)
    # except StreamlitAPIException:
    clicked = folder_picker.form_submit_button(folder_button_label, help=folder_button_help)

    if clicked:
        dirname = dirname.text_input(label, filedialog.askdirectory(master=root), help=help)
    else:
        dirname = dirname.text_input(label, value, help=help)

    return dirname


def get_sigmas_exponential(n, sigma_min, sigma_max, device='cpu'):
    """Constructs an exponential noise schedule."""
    sigmas = torch.linspace(math.log(sigma_max), math.log(sigma_min), n, device=device).exp()
    return append_zero(sigmas)


def get_sigmas_vp(n, beta_d=19.9, beta_min=0.1, eps_s=1e-3, device='cpu'):
    """Constructs a continuous VP noise schedule."""
    t = torch.linspace(1, eps_s, n, device=device)
    sigmas = torch.sqrt(torch.exp(beta_d * t ** 2 / 2 + beta_min * t) - 1)
    return append_zero(sigmas)


def to_d(x, sigma, denoised):
    """Converts a denoiser output to a Karras ODE derivative."""
    return (x - denoised) / append_dims(sigma, x.ndim)

def linear_multistep_coeff(order, t, i, j):
    if order - 1 > i:
        raise ValueError(f'Order {order} too high for step {i}')
    def fn(tau):
        prod = 1.
        for k in range(order):
            if j == k:
                continue
            prod *= (tau - t[i - k]) / (t[i - j] - t[i - k])
        return prod
    return integrate.quad(fn, t[i], t[i + 1], epsrel=1e-4)[0]

class KDiffusionSampler:
    def __init__(self, m, sampler):
        self.model = m
        self.model_wrap = K.external.CompVisDenoiser(m)
        self.schedule = sampler
    def get_sampler_name(self):
        return self.schedule
    def sample(self, S, conditioning, batch_size, shape, verbose, unconditional_guidance_scale, unconditional_conditioning, eta, x_T, img_callback=None, log_every_t=None):
        sigmas = self.model_wrap.get_sigmas(S)
        x = x_T * sigmas[0]
        model_wrap_cfg = CFGDenoiser(self.model_wrap)
        samples_ddim = None
        samples_ddim = K.sampling.__dict__[f'sample_{self.schedule}'](model_wrap_cfg, x, sigmas,
                                                                              extra_args={'cond': conditioning, 'uncond': unconditional_conditioning,
                                                                                          'cond_scale': unconditional_guidance_scale}, disable=False, callback=generation_callback)
        #
        return samples_ddim, None
#
#create class LDSR
class LDSR():
    #init function
    def __init__(self, modelPath,yamlPath):
        self.modelPath = modelPath
        self.yamlPath = yamlPath
        #self.model = self.load_model_from_config()
        #print(self.load_model_from_config(OmegaConf.load(yamlPath), modelPath))
        #self.print_current_directory()
    #get currennt directory

    '''
    def check_model_exists(self):
        #check if model and yaml exist
        path = self.pathInput + "/models/ldm/ld_sr".replace('\\',os.sep).replace('/',os.sep)
        model = self.modelName
        yaml = self.yamlName
        if os.path.exists(path):
            #check if yaml exists
            if os.path.exists(os.path.join(path,yaml)):
                print('YAML found')
                #check if ckpt exists
                if os.path.exists(os.path.join(path,model)):
                    print('Model found')
                    return os.path.join(path,model), os.path.join(path,yaml)
                else:
                    return False
        #return onlyfiles
    '''
    def load_model_from_config(self):
        #print(f"Loading model from {self.modelPath}")
        pl_sd = torch.load(self.modelPath, map_location="cpu")
        global_step = pl_sd["global_step"]
        sd = pl_sd["state_dict"]
        config = OmegaConf.load(self.yamlPath)
        model = instantiate_from_config(config.model)
        m, u = model.load_state_dict(sd, strict=False)
        model.cuda()
        model.eval()
        return {"model": model}#, global_step

    '''
    def get_model(self):
        check = self.check_model_exists()
        if check != False:
            path_ckpt = check[0]
            path_conf = check[1]
        else:
            print('Model not found, please run the bat file to download the model')
        config = OmegaConf.load(path_conf)
        model, step = self.load_model_from_config(config, path_ckpt)
        return model

    
    def get_custom_cond(mode):
        dest = "data/example_conditioning"

        if mode == "superresolution":
            uploaded_img = files.upload()
            filename = next(iter(uploaded_img))
            name, filetype = filename.split(".") # todo assumes just one dot in name !
            os.rename(f"{filename}", f"{dest}/{mode}/custom_{name}.{filetype}")

        elif mode == "text_conditional":
            #w = widgets.Text(value='A cake with cream!', disabled=True)
            w = 'Empty Test'
            display.display(w)

            with open(f"{dest}/{mode}/custom_{w.value[:20]}.txt", 'w') as f:
                f.write(w.value)

        elif mode == "class_conditional":
            #w = widgets.IntSlider(min=0, max=1000)
            w = 1000
            display.display(w)
            with open(f"{dest}/{mode}/custom.txt", 'w') as f:
                f.write(w.value)

        else:
            raise NotImplementedError(f"cond not implemented for mode{mode}")
    '''

    def get_cond_options(self,mode):
        path = "data/example_conditioning"
        path = os.path.join(path, mode)
        onlyfiles = [f for f in sorted(os.listdir(path))]
        return path, onlyfiles

    '''
    def select_cond_path(mode):
        path = "data/example_conditioning"  # todo
        path = os.path.join(path, mode)
        onlyfiles = [f for f in sorted(os.listdir(path))]

        selected = widgets.RadioButtons(
            options=onlyfiles,
            description='Select conditioning:',
            disabled=False
        )
        display.display(selected)
        selected_path = os.path.join(path, selected.value)
        return selected_path
    '''

    

    '''
    # Google Collab stuff
    def visualize_cond_img(path):
        display.display(ipyimg(filename=path))
    '''

    def run(self,model, selected_path, task, custom_steps, eta, resize_enabled=False, classifier_ckpt=None, global_step=None):
        def make_convolutional_sample(batch, model, mode="vanilla", custom_steps=None, eta=1.0, swap_mode=False, masked=False,
                              invert_mask=True, quantize_x0=False, custom_schedule=None, decode_interval=1000,
                              resize_enabled=False, custom_shape=None, temperature=1., noise_dropout=0., corrector=None,
                              corrector_kwargs=None, x_T=None, save_intermediate_vid=False, make_progrow=True,ddim_use_x0_pred=False):
            log = dict()

            z, c, x, xrec, xc = model.get_input(batch, model.first_stage_key,
                                                return_first_stage_outputs=True,
                                                force_c_encode=not (hasattr(model, 'split_input_params')
                                                                    and model.cond_stage_key == 'coordinates_bbox'),
                                                return_original_cond=True)

            log_every_t = 1 if save_intermediate_vid else None

            if custom_shape is not None:
                z = torch.randn(custom_shape)
                # print(f"Generating {custom_shape[0]} samples of shape {custom_shape[1:]}")

            z0 = None

            log["input"] = x
            log["reconstruction"] = xrec

            if ismap(xc):
                log["original_conditioning"] = model.to_rgb(xc)
                if hasattr(model, 'cond_stage_key'):
                    log[model.cond_stage_key] = model.to_rgb(xc)

            else:
                log["original_conditioning"] = xc if xc is not None else torch.zeros_like(x)
                if model.cond_stage_model:
                    log[model.cond_stage_key] = xc if xc is not None else torch.zeros_like(x)
                    if model.cond_stage_key =='class_label':
                        log[model.cond_stage_key] = xc[model.cond_stage_key]

            with model.ema_scope("Plotting"):
                t0 = time.time()
                img_cb = None

                sample, intermediates = convsample_ddim(model, c, steps=custom_steps, shape=z.shape,
                                                        eta=eta,
                                                        quantize_x0=quantize_x0, img_callback=img_cb, mask=None, x0=z0,
                                                        temperature=temperature, noise_dropout=noise_dropout,
                                                        score_corrector=corrector, corrector_kwargs=corrector_kwargs,
                                                        x_T=x_T, log_every_t=log_every_t)
                t1 = time.time()

                if ddim_use_x0_pred:
                    sample = intermediates['pred_x0'][-1]

            x_sample = model.decode_first_stage(sample)

            try:
                x_sample_noquant = model.decode_first_stage(sample, force_not_quantize=True)
                log["sample_noquant"] = x_sample_noquant
                log["sample_diff"] = torch.abs(x_sample_noquant - x_sample)
            except:
                pass

            log["sample"] = x_sample
            log["time"] = t1 - t0

            return log
        def convsample_ddim(model, cond, steps, shape, eta=1.0, callback=None, normals_sequence=None,
                    mask=None, x0=None, quantize_x0=False, img_callback=None,
                    temperature=1., noise_dropout=0., score_corrector=None,
                    corrector_kwargs=None, x_T=None, log_every_t=None
                    ):

            ddim = DDIMSampler(model)
            bs = shape[0]  # dont know where this comes from but wayne
            shape = shape[1:]  # cut batch dim
            print(f"Sampling with eta = {eta}; steps: {steps}")
            samples, intermediates = ddim.sample(steps, batch_size=bs, shape=shape, conditioning=cond, callback=callback,
                                                normals_sequence=normals_sequence, quantize_x0=quantize_x0, eta=eta,
                                                mask=mask, x0=x0, temperature=temperature, verbose=False,
                                                score_corrector=score_corrector,
                                                corrector_kwargs=corrector_kwargs, x_T=x_T)

            return samples, intermediates
        # global stride
        def get_cond(mode, selected_path):
            example = dict()
            if mode == "superresolution":
                up_f = 4
                #visualize_cond_img(selected_path)

                c = selected_path.convert('RGB')
                c = torch.unsqueeze(torchvision.transforms.ToTensor()(c), 0)
                c_up = torchvision.transforms.functional.resize(c, size=[up_f * c.shape[2], up_f * c.shape[3]], antialias=True)
                c_up = rearrange(c_up, '1 c h w -> 1 h w c')
                c = rearrange(c, '1 c h w -> 1 h w c')
                c = 2. * c - 1.

                c = c.to(torch.device("cuda"))
                example["LR_image"] = c
                example["image"] = c_up

            return example
        example = get_cond(task, selected_path)

        save_intermediate_vid = False
        n_runs = 1
        masked = False
        guider = None
        ckwargs = None
        mode = 'ddim'
        ddim_use_x0_pred = False
        temperature = 1.
        eta = eta
        make_progrow = True
        custom_shape = None

        height, width = example["image"].shape[1:3]
        split_input = height >= 128 and width >= 128

        if split_input:
            ks = 128
            stride = 64
            vqf = 4  #
            model.split_input_params = {"ks": (ks, ks), "stride": (stride, stride),
                                        "vqf": vqf,
                                        "patch_distributed_vq": True,
                                        "tie_braker": False,
                                        "clip_max_weight": 0.5,
                                        "clip_min_weight": 0.01,
                                        "clip_max_tie_weight": 0.5,
                                        "clip_min_tie_weight": 0.01}
        else:
            if hasattr(model, "split_input_params"):
                delattr(model, "split_input_params")

        invert_mask = False

        x_T = None
        for n in range(n_runs):
            if custom_shape is not None:
                x_T = torch.randn(1, custom_shape[1], custom_shape[2], custom_shape[3]).to(model.device)
                x_T = repeat(x_T, '1 c h w -> b c h w', b=custom_shape[0])

            logs = make_convolutional_sample(example, model,
                                         mode=mode, custom_steps=custom_steps,
                                         eta=eta, swap_mode=False , masked=masked,
                                         invert_mask=invert_mask, quantize_x0=False,
                                         custom_schedule=None, decode_interval=10,
                                         resize_enabled=resize_enabled, custom_shape=custom_shape,
                                         temperature=temperature, noise_dropout=0.,
                                         corrector=guider, corrector_kwargs=ckwargs, x_T=x_T, save_intermediate_vid=save_intermediate_vid,
                                         make_progrow=make_progrow,ddim_use_x0_pred=ddim_use_x0_pred
                                         )
        return logs


    @torch.no_grad()
    


    @torch.no_grad()
    
    def superResolution(self,image,ddimSteps=100,preDownScale='None',postDownScale='None'):
        diffMode = 'superresolution'
        model = self.load_model_from_config()
        #@title Import location
        #@markdown ***File height and width should be multiples of 64, or image will be padded.***

        #@markdown *To change upload settings without adding more, run and cancel upload*
        #import_method = 'Directory' #@param ['Google Drive', 'Upload']
        #output_subfolder_name = 'processed' #@param {type: 'string'}

        #@markdown Drive method options:
        #drive_directory = '/content/drive/MyDrive/upscaleTest' #@param {type: 'string'}

        #@markdown Upload method options:
        #remove_previous_uploads = False #@param {type: 'boolean'}
        #save_output_to_drive = False #@param {type: 'boolean'}
        #zip_if_not_drive = False #@param {type: 'boolean'}
        '''
        os.makedirs(pathInput+'/content/input'.replace('\\',os.sep).replace('/',os.sep), exist_ok=True)
        output_directory = os.getcwd()+f'/content/output/{output_subfolder_name}'.replace('\\',os.sep).replace('/',os.sep)
        os.makedirs(output_directory, exist_ok=True)
        uploaded_img = pathInput+'/content/input/'.replace('\\',os.sep).replace('/',os.sep)
        pathInput, dirsInput, filesInput = next(os.walk(pathInput+'/content/input').replace('\\',os.sep).replace('/',os.sep))
        file_count = len(filesInput)
        print(f'Found {file_count} files total')
        '''


        #Run settings

        diffusion_steps = int(ddimSteps) #@param [25, 50, 100, 250, 500, 1000]
        eta = 1.0 #@param  {type: 'raw'}
        stride = 0 #not working atm

        # ####Scaling options:
        # Downsampling to 256px first will often improve the final image and runs faster.
        
        # You can improve sharpness without upscaling by upscaling and then downsampling to the original size (i.e. Super Resolution)
        pre_downsample = preDownScale #@param ['None', '1/2', '1/4']

        post_downsample = postDownScale #@param ['None', 'Original Size', '1/2', '1/4']

        # Nearest gives sharper results, but may look more pixellated. Lancoz is much higher quality, but result may be less crisp.
        downsample_method = 'Lanczos' #@param ['Nearest', 'Lanczos']


        overwrite_prior_runs = True #@param {type: 'boolean'}

        #pathProcessed, dirsProcessed, filesProcessed = next(os.walk(output_directory))

        #for img in filesInput:
        #    if img in filesProcessed and overwrite_prior_runs is False:
        #        print(f'Skipping {img}: Already processed')
        #        continue
        gc.collect()
        torch.cuda.empty_cache()
        #dir = pathInput
        #filepath = os.path.join(dir, img).replace('\\',os.sep).replace('/',os.sep)

        im_og = image
        width_og, height_og = im_og.size

        #Downsample Pre
        if pre_downsample == '1/2':
            downsample_rate = 2
        elif pre_downsample == '1/4':
            downsample_rate = 4
        else:
            downsample_rate = 1
        # get system temp directory
        #dir = tempfile.gettempdir()
        width_downsampled_pre = width_og//downsample_rate
        height_downsampled_pre = height_og//downsample_rate
        if downsample_rate != 1:
            print(f'Downsampling from [{width_og}, {height_og}] to [{width_downsampled_pre}, {height_downsampled_pre}]')
            im_og = im_og.resize((width_downsampled_pre, height_downsampled_pre), Image.LANCZOS)
            #os.makedirs(dir, exist_ok=True)
            #im_og.save(dir + '/ldsr/temp.png'.replace('\\',os.sep).replace('/',os.sep))
            #filepath = dir + '/ldsr/temp.png'.replace('\\',os.sep).replace('/',os.sep)

        logs = self.run(model["model"], im_og, diffMode, diffusion_steps, eta)

        sample = logs["sample"]
        sample = sample.detach().cpu()
        sample = torch.clamp(sample, -1., 1.)
        sample = (sample + 1.) / 2. * 255
        sample = sample.numpy().astype(np.uint8)
        sample = np.transpose(sample, (0, 2, 3, 1))
        #print(sample.shape)
        a = Image.fromarray(sample[0])

        #Downsample Post
        if post_downsample == '1/2':
            downsample_rate = 2
        elif post_downsample == '1/4':
            downsample_rate = 4
        else:
            downsample_rate = 1

        width, height = a.size
        width_downsampled_post = width//downsample_rate
        height_downsampled_post = height//downsample_rate

        if downsample_method == 'Lanczos':
            aliasing = Image.LANCZOS
        else:
            aliasing = Image.NEAREST

        if downsample_rate != 1:
            print(f'Downsampling from [{width}, {height}] to [{width_downsampled_post}, {height_downsampled_post}]')
            a = a.resize((width_downsampled_post, height_downsampled_post), aliasing)
        elif post_downsample == 'Original Size':
            print(f'Downsampling from [{width}, {height}] to Original Size [{width_og}, {height_og}]')
            a = a.resize((width_og, height_og), aliasing)

        #display.display(a)
        #a.save(f'{output_directory}/{img}')
        del model
        gc.collect()
        torch.cuda.empty_cache()
        '''
        if import_method != 'Google Drive' and zip_if_not_drive is True:
        print('Zipping files')
        current_time = datetime.now().strftime('%y%m%d-%H%M%S_%f')
        output_zip_name = 'output'+str(current_time)+'.zip'
        #!zip -r {output_zip_name} {output_directory}
        print(f'Zipped outputs in {output_zip_name}')
        '''
        print(f'Processing finished!')
        return a


@torch.no_grad()
def log_likelihood(model, x, sigma_min, sigma_max, extra_args=None, atol=1e-4, rtol=1e-4):
    extra_args = {} if extra_args is None else extra_args
    s_in = x.new_ones([x.shape[0]])
    v = torch.randint_like(x, 2) * 2 - 1
    fevals = 0
    def ode_fn(sigma, x):
        nonlocal fevals
        with torch.enable_grad():
            x = x[0].detach().requires_grad_()
            denoised = model(x, sigma * s_in, **extra_args)
            d = to_d(x, sigma, denoised)
            fevals += 1
            grad = torch.autograd.grad((d * v).sum(), x)[0]
            d_ll = (v * grad).flatten(1).sum(1)
        return d.detach(), d_ll
    x_min = x, x.new_zeros([x.shape[0]])
    t = x.new_tensor([sigma_min, sigma_max])
    sol = odeint(ode_fn, x_min, t, atol=atol, rtol=rtol, method='dopri5')
    latent, delta_ll = sol[0][-1], sol[1][-1]
    ll_prior = torch.distributions.Normal(0, sigma_max).log_prob(latent).flatten(1).sum(1)
    return ll_prior + delta_ll, {'fevals': fevals}


def create_random_tensors(shape, seeds):
    xs = []
    for seed in seeds:
        torch.manual_seed(seed)

        # randn results depend on device; gpu and cpu get different results for same seed;
        # the way I see it, it's better to do this on CPU, so that everyone gets same result;
        # but the original script had it like this so i do not dare change it for now because
        # it will break everyone's seeds.
        xs.append(torch.randn(shape, device=st.session_state['defaults'].general.gpu))
    x = torch.stack(xs)
    return x

def torch_gc():
    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()
#
@retry(tries=5)
def generation_callback(img, i=0):
    if "update_preview_frequency" not in st.session_state:
        raise StopException

    model_manager: ModelRepo.Manager = server_state[ModelRepo.Manager.__name__]
    model_unet_name, model_cs_name, model_fs_name = get_active_sd_models()

    try:
        if i == 0:
            if img['i']: i = img['i']
    except TypeError:
        pass

    if st.session_state.update_preview and\
        int(st.session_state.update_preview_frequency) > 0 and\
        i % int(st.session_state.update_preview_frequency) == 0 and\
        i > 0:
        #print (img)
        #print (type(img))
        # The following lines will convert the tensor we got on img to an actual image we can render on the UI.
        # It can probably be done in a better way for someone who knows what they're doing. I don't.
        #print (img,isinstance(img, torch.Tensor))
        with model_manager.model_context(model_fs_name) as model:
            if isinstance(img, torch.Tensor):
                x_samples_ddim = model.decode_first_stage(img).to('cuda')
            else:
                # When using the k Diffusion samplers they return a dict instead of a tensor that look like this:
                # {'x': x, 'i': i, 'sigma': sigmas[i], 'sigma_hat': sigmas[i], 'denoised': denoised}
                x_samples_ddim = model.decode_first_stage(img["denoised"]).to('cuda')

        x_samples_ddim = torch.clamp((x_samples_ddim + 1.0) / 2.0, min=0.0, max=1.0)

        if x_samples_ddim.ndimension() == 4:
            pil_images = [transforms.ToPILImage()(x.squeeze_(0)) for x in x_samples_ddim]
            pil_image = image_grid(pil_images, 1)
        else:
            pil_image = transforms.ToPILImage()(x_samples_ddim.squeeze_(0))


        # update image on the UI so we can see the progress
        st.session_state["preview_image"].image(pil_image)

    # Show a progress bar so we can keep track of the progress even when the image progress is not been shown,
    # Dont worry, it doesnt affect the performance.
    if st.session_state["generation_mode"] == "txt2img":
        percent = int(100 * float(i+1 if i+1 < st.session_state.sampling_steps else st.session_state.sampling_steps)/float(st.session_state.sampling_steps))
        st.session_state["progress_bar_text"].text(
                    f"Running step: {i+1 if i+1 < st.session_state.sampling_steps else st.session_state.sampling_steps}/{st.session_state.sampling_steps} {percent if percent < 100 else 100}%")
    else:
        if st.session_state["generation_mode"] == "img2img":
            round_sampling_steps = round(st.session_state.sampling_steps * st.session_state["denoising_strength"])
            percent = int(100 * float(i+1 if i+1 < round_sampling_steps else round_sampling_steps)/float(round_sampling_steps))
            st.session_state["progress_bar_text"].text(
                            f"""Running step: {i+1 if i+1 < round_sampling_steps else round_sampling_steps}/{round_sampling_steps} {percent if percent < 100 else 100}%""")
        else:
            if st.session_state["generation_mode"] == "txt2vid":
                percent = int(100 * float(i+1 if i+1 < st.session_state.sampling_steps else st.session_state.sampling_steps)/float(st.session_state.sampling_steps))
                st.session_state["progress_bar_text"].text(
                                    f"Running step: {i+1 if i+1 < st.session_state.sampling_steps else st.session_state.sampling_steps}/{st.session_state.sampling_steps}"
                                        f"{percent if percent < 100 else 100}%")

    st.session_state["progress_bar"].progress(percent if percent < 100 else 100)


prompt_parser = re.compile("""
    (?P<prompt>                # capture group for 'prompt'
    [^:]+                      # match one or more non ':' characters
    )                          # end 'prompt'
    (?:                        # non-capture group
    :+                         # match one or more ':' characters
    (?P<weight>                # capture group for 'weight'
    -?\\d+(?:\\.\\d+)?            # match positive or negative decimal number
    )?                         # end weight capture group, make optional
    \\s*                        # strip spaces after weight
    |                          # OR
    $                          # else, if no ':' then match end of line
    )                          # end non-capture group
""", re.VERBOSE)

def split_weighted_subprompts(input_string, normalize=True):
    # grabs all text up to the first occurrence of ':' as sub-prompt
    # takes the value following ':' as weight
    # if ':' has no value defined, defaults to 1.0
    # repeats until no text remaining
    parsed_prompts = [(match.group("prompt"), float(match.group("weight") or 1)) for match in re.finditer(prompt_parser, input_string)]
    if not normalize:
        return parsed_prompts
    # this probably still doesn't handle negative weights very well
    weight_sum = sum(map(lambda x: x[1], parsed_prompts))
    return [(x[0], x[1] / weight_sum) for x in parsed_prompts]

def slerp(device, t, v0:torch.Tensor, v1:torch.Tensor, DOT_THRESHOLD=0.9995):
    v0 = v0.detach().cpu().numpy()
    v1 = v1.detach().cpu().numpy()

    dot = np.sum(v0 * v1 / (np.linalg.norm(v0) * np.linalg.norm(v1)))
    if np.abs(dot) > DOT_THRESHOLD:
        v2 = (1 - t) * v0 + t * v1
    else:
        theta_0 = np.arccos(dot)
        sin_theta_0 = np.sin(theta_0)
        theta_t = theta_0 * t
        sin_theta_t = np.sin(theta_t)
        s0 = np.sin(theta_0 - theta_t) / sin_theta_0
        s1 = sin_theta_t / sin_theta_0
        v2 = s0 * v0 + s1 * v1

    v2 = torch.from_numpy(v2).to(device)

    return v2

#
@st.experimental_memo(persist="disk", show_spinner=False, suppress_st_warning=True)
def optimize_update_preview_frequency(current_chunk_speed, previous_chunk_speed_list, update_preview_frequency, update_preview_frequency_list):
    """Find the optimal update_preview_frequency value maximizing
    performance while minimizing the time between updates."""
    from statistics import mean

    previous_chunk_avg_speed = mean(previous_chunk_speed_list)

    previous_chunk_speed_list.append(current_chunk_speed)
    current_chunk_avg_speed = mean(previous_chunk_speed_list)

    if current_chunk_avg_speed >= previous_chunk_avg_speed:
        #print(f"{current_chunk_speed} >= {previous_chunk_speed}")
        update_preview_frequency_list.append(update_preview_frequency + 1)
    else:
        #print(f"{current_chunk_speed} <= {previous_chunk_speed}")
        update_preview_frequency_list.append(update_preview_frequency - 1)

    update_preview_frequency = round(mean(update_preview_frequency_list))

    return current_chunk_speed, previous_chunk_speed_list, update_preview_frequency, update_preview_frequency_list


def get_font(fontsize):
    fonts = ["arial.ttf", "DejaVuSans.ttf"]
    for font_name in fonts:
        try:
            return ImageFont.truetype(font_name, fontsize)
        except OSError:
            pass

    # ImageFont.load_default() is practically unusable as it only supports
    # latin1, so raise an exception instead if no usable font was found
    raise Exception(f"No usable font found (tried {', '.join(fonts)})")

def load_embeddings(fp):
    with server_state[ModelRepo.Manager.__name__].model_context( ModelNames.SD_unet) as model:
        if fp is not None and hasattr(model, "embedding_manager"):
            model.embedding_manager.load(fp['name'])

def load_learned_embed_in_clip(learned_embeds_path, text_encoder, tokenizer, token=None):
    loaded_learned_embeds = torch.load(learned_embeds_path, map_location="cpu")

    # separate token and the embeds
    if learned_embeds_path.endswith('.pt'):
        print(loaded_learned_embeds['string_to_token'])
        trained_token = list(loaded_learned_embeds['string_to_token'].keys())[0]
        embeds = list(loaded_learned_embeds['string_to_param'].values())[0]

    elif learned_embeds_path.endswith('.bin'):
        trained_token = list(loaded_learned_embeds.keys())[0]
        embeds = loaded_learned_embeds[trained_token]

    embeds = loaded_learned_embeds[trained_token]
    # cast to dtype of text_encoder
    dtype = text_encoder.get_input_embeddings().weight.dtype
    embeds.to(dtype)

    # add the token in tokenizer
    token = token if token is not None else trained_token
    num_added_tokens = tokenizer.add_tokens(token)

    # resize the token embeddings
    text_encoder.resize_token_embeddings(len(tokenizer))

    # get the id for the token and assign the embeds
    token_id = tokenizer.convert_tokens_to_ids(token)
    text_encoder.get_input_embeddings().weight.data[token_id] = embeds
    return token

def image_grid(imgs, batch_size, force_n_rows=None, captions=None):
    #print (len(imgs))
    if force_n_rows is not None:
        rows = force_n_rows
    elif st.session_state['defaults'].general.n_rows > 0:
        rows = st.session_state['defaults'].general.n_rows
    elif st.session_state['defaults'].general.n_rows == 0:
        rows = batch_size
    else:
        rows = math.sqrt(len(imgs))
        rows = round(rows)

    cols = math.ceil(len(imgs) / rows)

    w, h = imgs[0].size
    grid = Image.new('RGB', size=(cols * w, rows * h), color='black')

    fnt = get_font(30)

    for i, img in enumerate(imgs):
        grid.paste(img, box=(i % cols * w, i // cols * h))
        if captions and i<len(captions):
            d = ImageDraw.Draw( grid )
            size = d.textbbox( (0,0), captions[i], font=fnt, stroke_width=2, align="center" )
            d.multiline_text((i % cols * w + w/2, i // cols * h + h - size[3]), captions[i], font=fnt, fill=(255,255,255), stroke_width=2, stroke_fill=(0,0,0), anchor="mm", align="center")

    return grid

def seed_to_int(s):
    if type(s) is int:
        return s
    if s is None or s == '':
        return random.randint(0, 2**32 - 1)

    if type(s) is list:
        seed_list = []
        for seed in s:
            if seed is None or seed == '':
                seed_list.append(random.randint(0, 2**32 - 1))
            else:
                seed_list = s

        return seed_list

    n = abs(int(s) if s.isdigit() else random.Random(s).randint(0, 2**32 - 1))
    while n >= 2**32:
        n = n >> 32
    return n

#
def draw_prompt_matrix(im, width, height, all_prompts):
    def wrap(text, d, font, line_length):
        lines = ['']
        for word in text.split():
            line = f'{lines[-1]} {word}'.strip()
            if d.textlength(line, font=font) <= line_length:
                lines[-1] = line
            else:
                lines.append(word)
        return '\n'.join(lines)

    def draw_texts(pos, x, y, texts, sizes):
        for i, (text, size) in enumerate(zip(texts, sizes)):
            active = pos & (1 << i) != 0

            if not active:
                text = '\u0336'.join(text) + '\u0336'

            d.multiline_text((x, y + size[1] / 2), text, font=fnt, fill=color_active if active else color_inactive, anchor="mm", align="center")

            y += size[1] + line_spacing

    fontsize = (width + height) // 25
    line_spacing = fontsize // 2
    fnt = get_font(fontsize)
    color_active = (0, 0, 0)
    color_inactive = (153, 153, 153)

    pad_top = height // 4
    pad_left = width * 3 // 4 if len(all_prompts) > 2 else 0

    cols = im.width // width
    rows = im.height // height

    prompts = all_prompts[1:]

    result = Image.new("RGB", (im.width + pad_left, im.height + pad_top), "white")
    result.paste(im, (pad_left, pad_top))

    d = ImageDraw.Draw(result)

    boundary = math.ceil(len(prompts) / 2)
    prompts_horiz = [wrap(x, d, fnt, width) for x in prompts[:boundary]]
    prompts_vert = [wrap(x, d, fnt, pad_left) for x in prompts[boundary:]]

    sizes_hor = [(x[2] - x[0], x[3] - x[1]) for x in [d.multiline_textbbox((0, 0), x, font=fnt) for x in prompts_horiz]]
    sizes_ver = [(x[2] - x[0], x[3] - x[1]) for x in [d.multiline_textbbox((0, 0), x, font=fnt) for x in prompts_vert]]
    hor_text_height = sum([x[1] + line_spacing for x in sizes_hor]) - line_spacing
    ver_text_height = sum([x[1] + line_spacing for x in sizes_ver]) - line_spacing

    for col in range(cols):
        x = pad_left + width * col + width / 2
        y = pad_top / 2 - hor_text_height / 2

        draw_texts(col, x, y, prompts_horiz, sizes_hor)

    for row in range(rows):
        x = pad_left / 2
        y = pad_top + height * row + height / 2 - ver_text_height / 2

        draw_texts(row, x, y, prompts_vert, sizes_ver)

    return result

#
def enable_minimal_memory_usage(model):
    """Moves only unet to fp16 and to CUDA, while keepping lighter models on CPUs"""
    model.unet.to(torch.float16).to(torch.device("cuda"))
    model.enable_attention_slicing(1)

    torch.cuda.empty_cache()
    torch_gc()

def check_prompt_length(prompt, comments):
    """this function tests if prompt is too long, and if so, adds a message to comments"""
    with server_state[ModelRepo.Manager.__name__].model_context( ModelNames.SD_cs) as model:
        tokenizer = model.cond_stage_model.tokenizer
        max_length = model.cond_stage_model.max_length

    info = tokenizer([prompt], truncation=True, max_length=max_length,
                     return_overflowing_tokens=True, padding="max_length", return_tensors="pt")
    ovf = info['overflowing_tokens'][0]
    overflowing_count = ovf.shape[0]
    if overflowing_count == 0:
        return

    vocab = {v: k for k, v in tokenizer.get_vocab().items()}
    overflowing_words = [vocab.get(int(x), "") for x in ovf]
    overflowing_text = tokenizer.convert_tokens_to_string(''.join(overflowing_words))

    comments.append(f"Warning: too many input tokens; some ({len(overflowing_words)}) have been truncated:\n{overflowing_text}\n")

#
def custom_models_available() -> Dict[str,str]:
    ret: Dict[str, str] = {}
    with server_state_lock["custom_models"]:
        #
        # Allow for custom models to be used instead of the default one,
        # an example would be Waifu-Diffusion or any other fine tune of stable diffusion
        server_state["custom_models"]:sorted = []

        for root, dirs, files in os.walk(os.path.join("models", "custom")):
            for file in files:
                name,ext = os.path.splitext(file)
                if ext == '.ckpt':
                    server_state["custom_models"].append(name)
                    ret[name] = os.path.join(root, file)

        with server_state_lock["CustomModel_available"]:
            if len(server_state["custom_models"]) > 0:
                server_state["CustomModel_available"] = True
                server_state["custom_models"].append("Stable Diffusion v1.4")
            else:
                server_state["CustomModel_available"] = False
    return ret
#
def GFPGAN_available() -> Dict[str,str]:
    # with server_state_lock["GFPGAN_models"]:
    #
    # Allow for custom models to be used instead of the default one,
    # an example would be Waifu-Diffusion or any other fine tune of stable diffusion
    ret: Dict[str, str] = {}
    st.session_state["GFPGAN_models"]:sorted = []

    for root, dirs, files in os.walk(st.session_state['defaults'].general.GFPGAN_dir):
        for file in files:
            name,ext = os.path.splitext(file)
            if ext == '.pth':
                st.session_state["GFPGAN_models"].append(name)
                ret[name] = os.path.join(root, file)

    #print (len(st.session_state["GFPGAN_models"]))
    # with server_state_lock["GFPGAN_available"]:
    if len(st.session_state["GFPGAN_models"]) > 0:
        st.session_state["GFPGAN_available"] = True
    else:
        st.session_state["GFPGAN_available"] = False
    return ret

#
def RealESRGAN_available() -> Dict[str,str]:
    # with server_state_lock["RealESRGAN_models"]:
    #
    # Allow for custom models to be used instead of the default one,
    # an example would be Waifu-Diffusion or any other fine tune of stable diffusion
    ret: Dict[str, str] = {}
    st.session_state["RealESRGAN_models"]:sorted = []

    for root, dirs, files in os.walk(st.session_state['defaults'].general.RealESRGAN_dir):
        for file in files:
            name,ext = os.path.splitext(file)
            if ext == '.pth':
                st.session_state["RealESRGAN_models"].append(name)
                ret[name] = os.path.join(root, file)

    # with server_state_lock["RealESRGAN_available"]:
    if len(st.session_state["RealESRGAN_models"]) > 0:
        st.session_state["RealESRGAN_available"] = True
    else:
        st.session_state["RealESRGAN_available"] = False
    return ret
#
def LDSR_available() -> Dict[str,str]:
    # with server_state_lock["RealESRGAN_models"]:
    #
    # Allow for custom models to be used instead of the default one,
    # an example would be Waifu-Diffusion or any other fine tune of stable diffusion
    ret: Dict[str, str] = {}
    st.session_state["LDSR_models"]:sorted = []

    for root, dirs, files in os.walk(st.session_state['defaults'].general.LDSR_dir):
        for file in files:
            name,ext = os.path.splitext(file)
            if ext == '.ckpt':
                st.session_state["LDSR_models"].append(name)
                ret[name] = os.path.join(root, file)

    #print (st.session_state['defaults'].general.LDSR_dir)
    #print (st.session_state["LDSR_models"])
    # with server_state_lock["LDSR_available"]:
    if len(st.session_state["LDSR_models"]) > 0:
        st.session_state["LDSR_available"] = True
    else:
        st.session_state["LDSR_available"] = False
    return ret

def save_sample(image, sample_path_i, filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback,
                save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode, save_individual_images, model_name):

    filename_i = os.path.join(sample_path_i, filename)

    if st.session_state['defaults'].general.save_metadata or write_info_files:
        # toggles differ for txt2img vs. img2img:
        offset = 0 if init_img is None else 2
        toggles = []
        if prompt_matrix:
            toggles.append(0)
        if normalize_prompt_weights:
            toggles.append(1)
        if init_img is not None:
            if uses_loopback:
                toggles.append(2)
            if uses_random_seed_loopback:
                toggles.append(3)
        if save_individual_images:
            toggles.append(2 + offset)
        if save_grid:
            toggles.append(3 + offset)
        if sort_samples:
            toggles.append(4 + offset)
        if write_info_files:
            toggles.append(5 + offset)
        if use_GFPGAN:
            toggles.append(6 + offset)
        metadata = \
                    dict(
                            target="txt2img" if init_img is None else "img2img",
                                prompt=prompts[i], ddim_steps=steps, toggles=toggles, sampler_name=sampler_name,
                                ddim_eta=ddim_eta, n_iter=n_iter, batch_size=batch_size, cfg_scale=cfg_scale,
                                seed=seeds[i], width=width, height=height, normalize_prompt_weights=normalize_prompt_weights, model_name=server_state["loaded_model"])
        # Not yet any use for these, but they bloat up the files:
        # info_dict["init_img"] = init_img
        # info_dict["init_mask"] = init_mask
        if init_img is not None:
            metadata["denoising_strength"] = str(denoising_strength)
            metadata["resize_mode"] = resize_mode

    if write_info_files:
        with open(f"{filename_i}.yaml", "w", encoding="utf8") as f:
            yaml.dump(metadata, f, allow_unicode=True, width=10000)

    if st.session_state['defaults'].general.save_metadata:
        # metadata = {
        # 	"SD:prompt": prompts[i],
        # 	"SD:seed": str(seeds[i]),
        # 	"SD:width": str(width),
        # 	"SD:height": str(height),
        # 	"SD:steps": str(steps),
        # 	"SD:cfg_scale": str(cfg_scale),
        # 	"SD:normalize_prompt_weights": str(normalize_prompt_weights),
        # }
        metadata = {"SD:" + k:v for (k,v) in metadata.items()}

        if save_ext == "png":
            mdata = PngInfo()
            for key in metadata:
                mdata.add_text(key, str(metadata[key]))
            image.save(f"{filename_i}.png", pnginfo=mdata)
        else:
            if jpg_sample:
                image.save(f"{filename_i}.jpg", quality=save_quality,
                                           optimize=True)
            elif save_ext == "webp":
                image.save(f"{filename_i}.{save_ext}", f"webp", quality=save_quality,
                                           lossless=save_lossless)
            else:
                # not sure what file format this is
                image.save(f"{filename_i}.{save_ext}", f"{save_ext}")
            try:
                exif_dict = piexif.load(f"{filename_i}.{save_ext}")
            except:
                exif_dict = { "Exif": dict() }
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(
                            json.dumps(metadata), encoding="unicode")
            piexif.insert(piexif.dump(exif_dict), f"{filename_i}.{save_ext}")


def get_next_sequence_number(path, prefix=''):
    """
    Determines and returns the next sequence number to use when saving an
    image in the specified directory.

    If a prefix is given, only consider files whose names start with that
    prefix, and strip the prefix from filenames before extracting their
    sequence number.

    The sequence starts at 0.
    """
    result = -1
    for p in Path(path).iterdir():
        if p.name.endswith(('.png', '.jpg')) and p.name.startswith(prefix):
            tmp = p.name[len(prefix):]
            try:
                result = max(int(tmp.split('-')[0]), result)
            except ValueError:
                pass
    return result + 1


def oxlamon_matrix(prompt, seed, n_iter, batch_size):
    pattern = re.compile(r'(,\s){2,}')

    class PromptItem:
        def __init__(self, text, parts, item):
            self.text = text
            self.parts = parts
            if item:
                self.parts.append( item )

    def clean(txt):
        return re.sub(pattern, ', ', txt)

    def getrowcount( txt ):
        for data in re.finditer( ".*?\\((.*?)\\).*", txt ):
            if data:
                return len(data.group(1).split("|"))
            break
        return None

    def repliter( txt ):
        for data in re.finditer( ".*?\\((.*?)\\).*", txt ):
            if data:
                r = data.span(1)
                for item in data.group(1).split("|"):
                    yield (clean(txt[:r[0]-1] + item.strip() + txt[r[1]+1:]), item.strip())
            break

    def iterlist( items ):
        outitems = []
        for item in items:
            for newitem, newpart in repliter(item.text):
                outitems.append( PromptItem(newitem, item.parts.copy(), newpart) )

        return outitems

    def getmatrix( prompt ):
        dataitems = [ PromptItem( prompt[1:].strip(), [], None ) ]
        while True:
            newdataitems = iterlist( dataitems )
            if len( newdataitems ) == 0:
                return dataitems
            dataitems = newdataitems

    def classToArrays( items, seed, n_iter ):
        texts = []
        parts = []
        seeds = []

        for item in items:
            itemseed = seed
            for i in range(n_iter):
                texts.append( item.text )
                parts.append( f"Seed: {itemseed}\n" + "\n".join(item.parts) )
                seeds.append( itemseed )
                itemseed += 1

        return seeds, texts, parts

    all_seeds, all_prompts, prompt_matrix_parts = classToArrays(getmatrix( prompt ), seed, n_iter)
    n_iter = math.ceil(len(all_prompts) / batch_size)

    needrows = getrowcount(prompt)
    if needrows:
        xrows = math.sqrt(len(all_prompts))
        xrows = round(xrows)
        # if columns is to much
        cols = math.ceil(len(all_prompts) / xrows)
        if cols > needrows*4:
            needrows *= 2

    return all_seeds, n_iter, prompt_matrix_parts, all_prompts, needrows

def get_active_sd_models() -> Tuple[str,str,str]:
    """Returns the ModelRepo-registered names of the currently selected SD model stages

    Returns:
        unet_name, cs_name, fs_name
    """
    custom_model_name = st.session_state["custom_model"]
    if st.session_state.defaults.general.optimized:
        unet_ext = "_unet"
        cs_ext = "_cs"
        fs_ext = "_fs"
    else:
        unet_ext = cs_ext = fs_ext = "_full"

    model_manager: ModelRepo.Manager = server_state[ModelRepo.Manager.__name__]
    model_unet_name = [x for x in [f"{custom_model_name}{unet_ext}", ModelNames.SD_unet] if model_manager.is_loadable(x)][0]
    model_cs_name = [x for x in [f"{custom_model_name}{cs_ext}", ModelNames.SD_cs] if model_manager.is_loadable(x)][0]
    model_fs_name = [x for x in [f"{custom_model_name}{fs_ext}", ModelNames.SD_fs] if model_manager.is_loadable(x)][0]
    return model_unet_name, model_cs_name, model_fs_name
#
def process_images(
    outpath, func_init, func_sample, prompt, seed, sampler_name, save_grid, batch_size,
        n_iter, steps, cfg_scale, width, height, prompt_matrix, use_GFPGAN: bool = True, GFPGAN_model: str = 'GFPGANv1.3', 
        use_RealESRGAN: bool = False, realesrgan_model_name:str = 'RealESRGAN_x4plus',
        use_LDSR:bool = False, LDSR_model_name:str = 'model', ddim_eta=0.0, normalize_prompt_weights=True, init_img=None, init_mask=None,
        mask_blur_strength=3, mask_restore=False, denoising_strength=0.75, noise_mode=0, find_noise_steps=1, resize_mode=None, uses_loopback=False,
        uses_random_seed_loopback=False, sort_samples=True, write_info_files=True, jpg_sample=False,
        variant_amount=0.0, variant_seed=None, save_individual_images: bool = True):
    """this is the main loop that both txt2img and img2img use; it calls func_init once inside all the scopes and func_sample once per batch"""
    
    torch_gc()
    # start time after garbage collection (or before?)
    start_time = time.time()

    # We will use this date here later for the folder name, need to start_time if not need
    run_start_dt = datetime.datetime.now()

    mem_mon = MemUsageMonitor('MemMon')
    mem_mon.start()

    model_manager: ModelRepo.Manager = server_state[ModelRepo.Manager.__name__]
    model_unet_name, model_cs_name, model_fs_name = get_active_sd_models()

    if st.session_state.defaults.general.use_sd_concepts_library:
        prompt_tokens = re.findall('<([a-zA-Z0-9-]+)>', prompt)
        if prompt_tokens:
            # compviz
            with model_manager.model_context(model_cs_name) as model:
                tokenizer = model.cond_stage_model.tokenizer
                text_encoder = model.cond_stage_model.transformer

            ext = ('pt', 'bin')

            if len(prompt_tokens) > 1:
                for token_name in prompt_tokens:
                    embedding_path = os.path.join(st.session_state['defaults'].general.sd_concepts_library_folder, token_name)
                    if os.path.exists(embedding_path):
                        for files in os.listdir(embedding_path):
                            if files.endswith(ext):
                                load_learned_embed_in_clip(f"{os.path.join(embedding_path, files)}", text_encoder, tokenizer, f"<{token_name}>")
            else:
                embedding_path = os.path.join(st.session_state['defaults'].general.sd_concepts_library_folder, prompt_tokens[0])
                if os.path.exists(embedding_path):
                    for files in os.listdir(embedding_path):
                        if files.endswith(ext):
                            load_learned_embed_in_clip(f"{os.path.join(embedding_path, files)}", text_encoder, tokenizer, f"<{prompt_tokens[0]}>")

        #


    os.makedirs(outpath, exist_ok=True)

    sample_path = os.path.join(outpath, "samples")
    os.makedirs(sample_path, exist_ok=True)

    if not ("|" in prompt) and prompt.startswith("@"):
        prompt = prompt[1:]

    negprompt = ''
    if '###' in prompt:
        prompt, negprompt = prompt.split('###', 1)
        prompt = prompt.strip()
        negprompt = negprompt.strip()

    comments = []

    prompt_matrix_parts = []
    simple_templating = False
    add_original_image = not (use_RealESRGAN or use_GFPGAN)

    if prompt_matrix:
        if prompt.startswith("@"):
            simple_templating = True
            add_original_image = not (use_RealESRGAN or use_GFPGAN)
            all_seeds, n_iter, prompt_matrix_parts, all_prompts, frows = oxlamon_matrix(prompt, seed, n_iter, batch_size)
        else:
            all_prompts = []
            prompt_matrix_parts = prompt.split("|")
            combination_count = 2 ** (len(prompt_matrix_parts) - 1)
            for combination_num in range(combination_count):
                current = prompt_matrix_parts[0]

                for n, text in enumerate(prompt_matrix_parts[1:]):
                    if combination_num & (2 ** n) > 0:
                        current += ("" if text.strip().startswith(",") else ", ") + text

                all_prompts.append(current)

            n_iter = math.ceil(len(all_prompts) / batch_size)
            all_seeds = len(all_prompts) * [seed]

        print(f"Prompt matrix will create {len(all_prompts)} images using a total of {n_iter} batches.")
    else:

        if not st.session_state['defaults'].general.no_verify_input:
            try:
                check_prompt_length(prompt, comments)
            except:
                import traceback
                print("Error verifying input:", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)

        all_prompts = batch_size * n_iter * [prompt]
        all_seeds = [seed + x for x in range(len(all_prompts))]

    precision_scope = autocast if st.session_state['defaults'].general.precision == "autocast" else nullcontext
    output_images = []
    grid_captions = []
    stats = []
    with ExitStack() as stack:
        stack.enter_context( torch.no_grad() )
        stack.enter_context( precision_scope("cuda") )
        model = stack.enter_context( model_manager.model_context(model_unet_name) )
        if not st.session_state['defaults'].general.optimized:
            stack.enter_context(model.ema_scope())

        init_data = func_init()
        tic = time.time()


        # if variant_amount > 0.0 create noise from base seed
        base_x = None
        if variant_amount > 0.0:
            target_seed_randomizer = seed_to_int('') # random seed
            torch.manual_seed(seed) # this has to be the single starting seed (not per-iteration)
            base_x = create_random_tensors([opt_C, height // opt_f, width // opt_f], seeds=[seed])
            # we don't want all_seeds to be sequential from starting seed with variants,
            # since that makes the same variants each time,
            # so we add target_seed_randomizer as a random offset
            for si in range(len(all_seeds)):
                all_seeds[si] += target_seed_randomizer

        for n in range(n_iter):
            print(f"Iteration: {n+1}/{n_iter}")
            prompts = all_prompts[n * batch_size:(n + 1) * batch_size]
            captions = prompt_matrix_parts[n * batch_size:(n + 1) * batch_size]
            seeds = all_seeds[n * batch_size:(n + 1) * batch_size]

            print(prompt)

            with model_manager.model_context(model_cs_name) as model:
                uc = model.get_learned_conditioning(len(prompts) * [negprompt])

            if isinstance(prompts, tuple):
                prompts = list(prompts)

            # split the prompt if it has : for weighting
            # TODO for speed it might help to have this occur when all_prompts filled??
            weighted_subprompts = split_weighted_subprompts(prompts[0], normalize_prompt_weights)

            # sub-prompt weighting used if more than 1
            with model_manager.model_context(model_cs_name) as model:
                if len(weighted_subprompts) > 1:
                    c = torch.zeros_like(uc) # i dont know if this is correct.. but it works
                    for i in range(0, len(weighted_subprompts)):
                        # note if alpha negative, it functions same as torch.sub
                        c = torch.add(c, model.get_learned_conditioning(weighted_subprompts[i][0]), alpha=weighted_subprompts[i][1])
                else: # just behave like usual
                    c = model.get_learned_conditioning(prompts)

            shape = [opt_C, height // opt_f, width // opt_f]

            if noise_mode == 1 or noise_mode == 3:
                # TODO params for find_noise_to_image
                with model_manager.model_context( model_unet_name ) as model:
                    x = torch.cat(batch_size * [find_noise_for_image(
                                            model, server_state["device"], # TODO: ModelManager device
                                            init_img.convert('RGB'), '', find_noise_steps, 0.0, normalize=True,
                                            generation_callback=generation_callback,
                                            )], dim=0)
            else:
                # we manually generate all input noises because each one should have a specific seed
                x = create_random_tensors(shape, seeds=seeds)

            if variant_amount > 0.0: # we are making variants
                # using variant_seed as sneaky toggle,
                # when not None or '' use the variant_seed
                # otherwise use seeds
                if variant_seed != None and variant_seed != '':
                    specified_variant_seed = seed_to_int(variant_seed)
                    torch.manual_seed(specified_variant_seed)
                    seeds = [specified_variant_seed]
                # finally, slerp base_x noise to target_x noise for creating a variant
                x = slerp(st.session_state['defaults'].general.gpu, max(0.0, min(1.0, variant_amount)), base_x, x)

            samples_ddim = func_sample(init_data=init_data, x=x, conditioning=c, unconditional_conditioning=uc, sampler_name=sampler_name)

            with model_manager.model_context(model_fs_name) as model:
                x_samples_ddim = model.decode_first_stage(samples_ddim)
            x_samples_ddim = torch.clamp((x_samples_ddim + 1.0) / 2.0, min=0.0, max=1.0)

            run_images = []
            for i, x_sample in enumerate(x_samples_ddim):
                sanitized_prompt = slugify(prompts[i])

                percent = i / len(x_samples_ddim)
                st.session_state["progress_bar"].progress(percent if percent < 100 else 100)

                if sort_samples:
                    full_path = os.path.join(os.getcwd(), sample_path, sanitized_prompt)


                    sanitized_prompt = sanitized_prompt[:200-len(full_path)]
                    sample_path_i = os.path.join(sample_path, sanitized_prompt)

                    #print(f"output folder length: {len(os.path.join(os.getcwd(), sample_path_i))}")
                    #print(os.path.join(os.getcwd(), sample_path_i))

                    os.makedirs(sample_path_i, exist_ok=True)
                    base_count = get_next_sequence_number(sample_path_i)
                    filename = f"{base_count:05}-{steps}_{sampler_name}_{seeds[i]}"
                else:
                    full_path = os.path.join(os.getcwd(), sample_path)
                    sample_path_i = sample_path
                    base_count = get_next_sequence_number(sample_path_i)
                    filename = f"{base_count:05}-{steps}_{sampler_name}_{seeds[i]}_{sanitized_prompt}"[:200-len(full_path)] #same as before

                x_sample = 255. * rearrange(x_sample.cpu().numpy(), 'c h w -> h w c')
                x_sample = x_sample.astype(np.uint8)
                image = Image.fromarray(x_sample)
                original_sample = x_sample
                original_filename = filename

                st.session_state["preview_image"].image(image)

                if use_GFPGAN and model_manager.is_loadable(GFPGAN_model) and not use_RealESRGAN:
                    st.session_state["progress_bar_text"].text("Running GFPGAN on image %d of %d..." % (i+1, len(x_samples_ddim)))
                    # skip_save = True # #287 >_>
                    torch_gc()
                    with model_manager.model_context(GFPGAN_model) as model:
                        cropped_faces, restored_faces, restored_img = model.enhance(x_sample[:,:,::-1], has_aligned=False, only_center_face=False, paste_back=True)
                    gfpgan_sample = restored_img[:,:,::-1]
                    gfpgan_image = Image.fromarray(gfpgan_sample)
                    
                    #if st.session_state["GFPGAN_strenght"]:
                        #gfpgan_sample = Image.blend(image, gfpgan_image, st.session_state["GFPGAN_strenght"])                    
                        
                    gfpgan_filename = original_filename + '-gfpgan'

                    save_sample(gfpgan_image, sample_path_i, gfpgan_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                                                    normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback,
                                                    uses_random_seed_loopback, save_grid, sort_samples, sampler_name, ddim_eta,
                                                    n_iter, batch_size, i, denoising_strength, resize_mode, False, server_state["loaded_model"])

                    output_images.append(gfpgan_image) #287
                    run_images.append(gfpgan_image)

                    if simple_templating:
                        grid_captions.append( captions[i] + "\ngfpgan" )
                        
                #
                elif use_GFPGAN and model_manager.is_loadable(GFPGAN_model) and not use_LDSR:
                    st.session_state["progress_bar_text"].text("Running GFPGAN on image %d of %d..." % (i+1, len(x_samples_ddim)))

                    torch_gc()
                    with model_manager.model_context(GFPGAN_model) as model:
                        cropped_faces, restored_faces, restored_img = model.enhance(x_sample[:,:,::-1], has_aligned=False, only_center_face=False, paste_back=True)

                    gfpgan_sample = restored_img[:,:,::-1]
                    gfpgan_image = Image.fromarray(gfpgan_sample)
                    
                    #if st.session_state["GFPGAN_strenght"]:
                        #gfpgan_sample = Image.blend(image, gfpgan_image, st.session_state["GFPGAN_strenght"])                    
                        
                    gfpgan_filename = original_filename + '-gfpgan'

                    save_sample(gfpgan_image, sample_path_i, gfpgan_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                                                    normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback,
                                                    uses_random_seed_loopback, save_grid, sort_samples, sampler_name, ddim_eta,
                                                    n_iter, batch_size, i, denoising_strength, resize_mode, False, server_state["loaded_model"])

                    output_images.append(gfpgan_image) #287
                    run_images.append(gfpgan_image)

                    if simple_templating:
                        grid_captions.append( captions[i] + "\ngfpgan" )                

                elif use_RealESRGAN and model_manager.is_loadable(realesrgan_model_name) and not use_GFPGAN:
                    st.session_state["progress_bar_text"].text("Running RealESRGAN on image %d of %d..." % (i+1, len(x_samples_ddim)))
                    # skip_save = True # #287 >_>
                    torch_gc()

                    with model_manager.model_context(realesrgan_model_name) as model:
                        output, img_mode = model.enhance(x_sample[:,:,::-1])
                    esrgan_filename = original_filename + '-esrgan4x'
                    esrgan_sample = output[:,:,::-1]
                    esrgan_image = Image.fromarray(esrgan_sample)

                    # save_sample(image, sample_path_i, original_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                            #normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback, skip_save,
                            # save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode)

                    save_sample(esrgan_image, sample_path_i, esrgan_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                                                    normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback,
                                                    save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode, False, server_state["loaded_model"])

                    output_images.append(esrgan_image) #287
                    run_images.append(esrgan_image)

                    if simple_templating:
                        grid_captions.append( captions[i] + "\nesrgan" )
                        
                #
                elif use_LDSR and model_manager.is_loadable(LDSR_model_name)is not None and not use_GFPGAN:
                    print ("Running LDSR on image %d of %d..." % (i+1, len(x_samples_ddim)))
                    st.session_state["progress_bar_text"].text("Running LDSR on image %d of %d..." % (i+1, len(x_samples_ddim)))
                    #skip_save = True # #287 >_>
                    torch_gc()
                    with model_manager.model_context(LDSR_model_name) as model:
                        ldsr_image = model.superResolution(image, 2, 2, 2)
                    ldsr_filename = original_filename + '-ldsr4x'

                    #save_sample(image, sample_path_i, original_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                            #normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback, skip_save,
                            #save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode)

                    save_sample(ldsr_image, sample_path_i, ldsr_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                                                    normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback,
                                                    save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode, False, server_state["loaded_model"])

                    output_images.append(ldsr_image) #287
                    run_images.append(ldsr_image)

                    if simple_templating:
                        grid_captions.append( captions[i] + "\nldsr" )

                elif use_RealESRGAN and model_manager.is_loadable(realesrgan_model_name) and use_GFPGAN and model_manager.is_loadable(ModelNames.GFPGAN):
                    st.session_state["progress_bar_text"].text("Running GFPGAN+RealESRGAN on image %d of %d..." % (i+1, len(x_samples_ddim)))
                    # skip_save = True # #287 >_>
                    torch_gc()
                    with model_manager.model_context(GFPGAN_model) as model:
                        cropped_faces, restored_faces, restored_img = model.enhance(x_sample[:,:,::-1], has_aligned=False, only_center_face=False, paste_back=True)
                    gfpgan_sample = restored_img[:,:,::-1]

                    with model_manager.model_context(realesrgan_model_name) as model:
                        output, img_mode = model.enhance(gfpgan_sample[:,:,::-1])

                    gfpgan_esrgan_filename = original_filename + '-gfpgan-esrgan4x'
                    gfpgan_esrgan_sample = output[:,:,::-1]
                    gfpgan_esrgan_image = Image.fromarray(gfpgan_esrgan_sample)

                    save_sample(gfpgan_esrgan_image, sample_path_i, gfpgan_esrgan_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                                                    normalize_prompt_weights, False, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback,
                                                    save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode, False, server_state["loaded_model"])

                    output_images.append(gfpgan_esrgan_image) #287
                    run_images.append(gfpgan_esrgan_image)

                    if simple_templating:
                        grid_captions.append( captions[i] + "\ngfpgan_esrgan" )
                        
                #
                    elif use_LDSR and model_manager.is_loadable(LDSR_model_name) and use_GFPGAN and model_manager.is_loadable(realesrgan_model_name):
                        st.session_state["progress_bar_text"].text("Running GFPGAN+LDSR on image %d of %d..." % (i+1, len(x_samples_ddim)))
                        #skip_save = True # #287 >_>
                        # skip_save = True # #287 >_>
                        with model_manager.model_context(GFPGAN_model) as model:
                            cropped_faces, restored_faces, restored_img = model.enhance(x_sample[:,:,::-1], has_aligned=False, only_center_face=False, paste_back=True)
                        gfpgan_sample = restored_img[:,:,::-1]

                        with model_manager.model_context(LDSR_model_name) as model:
                            gfpgan_ldsr_image = model.superResolution(Image.fromarray(gfpgan_sample))
                        gfpgan_ldsr_filename = original_filename + '-gfpgan-ldsr4x'

                        save_sample(gfpgan_ldsr_image, sample_path_i, gfpgan_ldsr_filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                                                        normalize_prompt_weights, False, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback,
                                                        save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode, False, server_state["loaded_model"])
    
                        output_images.append(gfpgan_ldsr_image) #287
                        run_images.append(gfpgan_ldsr_image)
    
                        if simple_templating:
                            grid_captions.append( captions[i] + "\ngfpgan_ldsr" )                
                
                else:
                    output_images.append(image)
                    run_images.append(image)

                if mask_restore and init_mask:
                    #init_mask = init_mask if keep_mask else ImageOps.invert(init_mask)
                    init_mask = init_mask.filter(ImageFilter.GaussianBlur(mask_blur_strength))
                    init_mask = init_mask.convert('L')
                    init_img = init_img.convert('RGB')
                    image = image.convert('RGB')

                    if use_RealESRGAN and model_manager.is_loadable(realesrgan_model_name):
                        with model_manager.model_context(realesrgan_model_name) as model:
                            output, img_mode = model.enhance(np.array(init_img, dtype=np.uint8))
                            init_img = Image.fromarray(output)
                            init_img = init_img.convert('RGB')

                            output, img_mode = model.enhance(np.array(init_mask, dtype=np.uint8))
                            init_mask = Image.fromarray(output)
                            init_mask = init_mask.convert('L')

                    image = Image.composite(init_img, image, init_mask)

                if save_individual_images:
                    save_sample(image, sample_path_i, filename, jpg_sample, prompts, seeds, width, height, steps, cfg_scale,
                                                    normalize_prompt_weights, use_GFPGAN, write_info_files, prompt_matrix, init_img, uses_loopback, uses_random_seed_loopback,
                                                    save_grid, sort_samples, sampler_name, ddim_eta, n_iter, batch_size, i, denoising_strength, resize_mode, save_individual_images, server_state["loaded_model"])

                    # if add_original_image or not simple_templating:
                        # output_images.append(image)
                        # if simple_templating:
                            #grid_captions.append( captions[i] )

            if len(run_images) > 1:
                preview_image = image_grid(run_images, n_iter)
            else:
                preview_image = run_images[0]

            # Constrain the final preview image to 1440x900 so we're not sending huge amounts of data
            # to the browser
            preview_image = constrain_image(preview_image, 1440, 900)
            st.session_state["progress_bar_text"].text("Finished!")
            st.session_state["preview_image"].image(preview_image)

        if prompt_matrix or save_grid:
            if prompt_matrix:
                if simple_templating:
                    grid = image_grid(output_images, n_iter, force_n_rows=frows, captions=grid_captions)
                else:
                    grid = image_grid(output_images, n_iter, force_n_rows=1 << ((len(prompt_matrix_parts)-1)//2))
                    try:
                        grid = draw_prompt_matrix(grid, width, height, prompt_matrix_parts)
                    except:
                        import traceback
                        print("Error creating prompt_matrix text:", file=sys.stderr)
                        print(traceback.format_exc(), file=sys.stderr)
            else:
                grid = image_grid(output_images, batch_size)

            if grid and (batch_size > 1  or n_iter > 1):
                output_images.insert(0, grid)

            grid_count = get_next_sequence_number(outpath, 'grid-')
            grid_file = f"grid-{grid_count:05}-{seed}_{slugify(prompts[i].replace(' ', '_')[:200-len(full_path)])}.{grid_ext}"
            grid.save(os.path.join(outpath, grid_file), grid_format, quality=grid_quality, lossless=grid_lossless, optimize=True)

        toc = time.time()

    mem_max_used, mem_total = mem_mon.read_and_stop()
    time_diff = time.time()-start_time

    info = f"""
            {prompt}
            Steps: {steps}, Sampler: {sampler_name}, CFG scale: {cfg_scale}, Seed: {seed}{', Denoising strength: '+str(denoising_strength) if init_img is not None else ''}{', GFPGAN' if use_GFPGAN and model_manager.is_loadable(ModelNames.GFPGAN) else ''}{', '+realesrgan_model_name if use_RealESRGAN and model_manager.is_loadable(realesrgan_model_name) else ''}{', Prompt Matrix Mode.' if prompt_matrix else ''}""".strip()
    stats = f'''
            Took { round(time_diff, 2) }s total ({ round(time_diff/(len(all_prompts)),2) }s per image)
            Peak memory usage: { -(mem_max_used // -1_048_576) } MiB / { -(mem_total // -1_048_576) } MiB / { round(mem_max_used/mem_total*100, 3) }%'''

    for comment in comments:
        info += "\n\n" + comment

    # mem_mon.stop()
    #del mem_mon
    torch_gc()

    return output_images, seed, info, stats


def resize_image(resize_mode, im, width, height):
    LANCZOS = (Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
    if resize_mode == 0:
        res = im.resize((width, height), resample=LANCZOS)
    elif resize_mode == 1:
        ratio = width / height
        src_ratio = im.width / im.height

        src_w = width if ratio > src_ratio else im.width * height // im.height
        src_h = height if ratio <= src_ratio else im.height * width // im.width

        resized = im.resize((src_w, src_h), resample=LANCZOS)
        res = Image.new("RGBA", (width, height))
        res.paste(resized, box=(width // 2 - src_w // 2, height // 2 - src_h // 2))
    else:
        ratio = width / height
        src_ratio = im.width / im.height

        src_w = width if ratio < src_ratio else im.width * height // im.height
        src_h = height if ratio >= src_ratio else im.height * width // im.width

        resized = im.resize((src_w, src_h), resample=LANCZOS)
        res = Image.new("RGBA", (width, height))
        res.paste(resized, box=(width // 2 - src_w // 2, height // 2 - src_h // 2))

        if ratio < src_ratio:
            fill_height = height // 2 - src_h // 2
            res.paste(resized.resize((width, fill_height), box=(0, 0, width, 0)), box=(0, 0))
            res.paste(resized.resize((width, fill_height), box=(0, resized.height, width, resized.height)), box=(0, fill_height + src_h))
        elif ratio > src_ratio:
            fill_width = width // 2 - src_w // 2
            res.paste(resized.resize((fill_width, height), box=(0, 0, 0, height)), box=(0, 0))
            res.paste(resized.resize((fill_width, height), box=(resized.width, 0, resized.width, height)), box=(fill_width + src_w, 0))

    return res

def constrain_image(img, max_width, max_height):
    ratio = max(img.width / max_width, img.height / max_height)
    if ratio <= 1:
        return img
    resampler = (Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
    resized = img.resize((int(img.width / ratio), int(img.height / ratio)), resample=resampler)
    return resized

def convert_pt_to_bin_and_load(input_file, text_encoder, tokenizer, placeholder_token):
    x = torch.load(input_file, map_location=torch.device('cpu'))

    params_dict = {
        placeholder_token: torch.tensor(list(x['string_to_param'].items())[0][1])
    }
    torch.save(params_dict, "learned_embeds.bin")
    load_learned_embed_in_clip("learned_embeds.bin", text_encoder, tokenizer, placeholder_token)
    print("loaded", placeholder_token)
