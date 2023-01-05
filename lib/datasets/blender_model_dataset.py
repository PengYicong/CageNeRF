import json

import cv2
import imageio
import torch.utils.data as data
import torch
import numpy as np
import os
from lib.config import cfg
from lib.utils.if_nerf import if_nerf_data_utils as if_nerf_dutils


class Dataset(data.Dataset):
    def __init__(self,
                 data_root,
                 split='train',
                 val_interval=5):
        super(Dataset, self).__init__()

        self.data_root = data_root
        self.split = split
        self.val_interval = val_interval
        self.nrays = cfg.N_rand
        self.metadata = self.read_meta()
        # self.norm_field = np.load(os.path.join(self.data_root, "norm.npy")).astype(np.float32)
        self.camera_K = np.array(self.metadata['K']).astype(np.float32)
        self.model_R = np.array(self.metadata['Rh']).astype(np.float32)
        self.model_T = np.array(self.metadata['Th']).astype(np.float32)
        self.model_R_inv = np.linalg.inv(self.model_R)
        self.img_list, self.mask_list, self.camera_R, self.camera_T = self.read_image_mask_campose()

    def read_meta(self):
        with open(os.path.join(self.data_root, "annots_{}.json".format(self.split)), 'r') as f:
            return json.load(f)

    def read_image_mask_campose(self):
        image_paths = []
        mask_paths = []
        camera_rotations = []
        camera_translations = []
        skip = self.val_interval if self.split == 'val' else 1
        for frame in self.metadata['frames'][::skip]:
            image_paths.append(os.path.join(self.data_root, 'new_image', frame['img_id']))
            mask_paths.append(os.path.join(self.data_root, 'mask', frame['img_id']))
            camera_rotations.append(frame['R'])
            camera_translations.append(frame['T'])
        camera_rotations = np.array(camera_rotations).astype(np.float32)
        camera_translations = np.array(camera_translations).astype(np.float32)

        return image_paths, mask_paths, camera_rotations, camera_translations

    def get_mask(self, idx):
        mask_blender = imageio.imread(self.mask_list[idx])
        mask_blender = (mask_blender != 0).astype(np.uint8)
        mask_blender = np.array(mask_blender)
        edge_mask = mask_blender.copy()
        if not cfg.eval and cfg.erode_edge:
            kernel = np.ones((5, 5), np.uint8)
            mask_erode = cv2.erode(edge_mask.copy(), kernel)
            mask_dilate = cv2.dilate(edge_mask.copy(), kernel)
            edge_mask[(mask_dilate - mask_erode) == 1] = 100
        return edge_mask, mask_blender

    def prepare_input(self):
        rxyz = np.load(os.path.join(self.data_root, 'r_vertices.npy')).astype(np.float32)
        wxyz = np.dot(rxyz - self.model_T, self.model_R)
        return rxyz, wxyz

    def __len__(self):
        return len(self.img_list)

    def __getitem__(self, idx):
        image = np.array(imageio.imread(self.img_list[idx]))
        image = image.astype(np.float32) / 255.0
        mask, orig_mask = self.get_mask(idx)

        H, W = image.shape[:2]

        K = self.camera_K
        R = self.camera_R[idx]
        T = self.camera_T[idx][:, None]

        rpts, wpts = self.prepare_input()

        rbounds = if_nerf_dutils.get_bounds(rpts)
        wbounds = if_nerf_dutils.get_bounds(wpts)

        rgb, ray_o, ray_d, near, far, coord, mask_at_box = if_nerf_dutils.sample_ray_blender(
            image, mask, K, R, T, wbounds, self.nrays, self.split)

        if cfg.erode_edge:
            orig_mask = if_nerf_dutils.crop_mask_edge(orig_mask)

        occupancy = orig_mask[coord[:, 0], coord[:, 1]]

        # nerf data
        ret = {
            'rgb': rgb,
            'occupancy': occupancy,
            'ray_o': ray_o,
            'ray_d': ray_d,
            'near': near,
            'far': far,
            'mask_at_box': mask_at_box,
            # 'norm_field': self.norm_field,
            'latent_index': idx
        }

        # meta data
        meta = {
            'rbounds': rbounds,
            'wbounds': wbounds,
            'frame_idx': idx
        }

        ret.update(meta)

        # transformation
        meta = {
            'R': self.model_R,
            'R_inv': self.model_R_inv,
            'Th': self.model_T,
            'H': H,
            'W': W
        }

        ret.update(meta)

        return ret
