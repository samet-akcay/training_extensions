# Copyright (C) 2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Test of RTMDetHead."""

import pytest
import torch
from omegaconf import DictConfig
from otx.algo.detection.heads.base_sampler import PseudoSampler
from otx.algo.detection.heads.distance_point_bbox_coder import DistancePointBBoxCoder
from otx.algo.detection.heads.dynamic_soft_label_assigner import DynamicSoftLabelAssigner
from otx.algo.detection.heads.point_generator import MlvlPointGenerator
from otx.algo.detection.heads.rtmdet_head import RTMDetHead, RTMDetSepBNHead
from otx.algo.detection.losses.gfocal_loss import QualityFocalLoss
from otx.algo.detection.losses.iou_loss import GIoULoss


@pytest.fixture()
def input_features():
    batch_size = 2
    in_channels = 96  # Match the in_channels of the rtmdet_sep_bn_head fixture
    feat_size = [32, 64, 128]  # Example feature map sizes
    return [torch.rand(batch_size, in_channels, size, size) for size in feat_size]


class TestRTMDetHead:
    @pytest.fixture()
    def rtmdet_head(self) -> RTMDetHead:
        train_cfg = {
            "assigner": DynamicSoftLabelAssigner(topk=13),
            "sampler": PseudoSampler(),
            "allowed_border": -1,
            "pos_weight": -1,
            "debug": False,
        }

        test_cfg = DictConfig(
            {
                "nms": {"type": "nms", "iou_threshold": 0.65},
                "score_thr": 0.001,
                "mask_thr_binary": 0.5,
                "max_per_img": 300,
                "min_bbox_size": 0,
                "nms_pre": 30000,
            },
        )
        return RTMDetHead(
            num_classes=80,
            in_channels=96,
            stacked_convs=2,
            feat_channels=96,
            anchor_generator=MlvlPointGenerator(offset=0, strides=[8, 16, 32]),
            bbox_coder=DistancePointBBoxCoder(),
            loss_cls=QualityFocalLoss(use_sigmoid=True, beta=2.0, loss_weight=1.0),
            loss_bbox=GIoULoss(loss_weight=2.0),
            with_objectness=False,
            pred_kernel_size=1,
            norm_cfg={"type": "BN"},
            act_cfg={"type": "SiLU", "inplace": True},
            train_cfg=train_cfg,
            test_cfg=test_cfg,
        )

    def test_forward(self, rtmdet_head, input_features) -> None:
        cls_scores, bbox_preds = rtmdet_head(input_features)
        assert len(cls_scores) == len(input_features)
        assert len(bbox_preds) == len(input_features)
        for cls_score, bbox_pred in zip(cls_scores, bbox_preds):
            assert cls_score.shape[1] == rtmdet_head.num_base_priors * rtmdet_head.cls_out_channels
            assert bbox_pred.shape[1] == rtmdet_head.num_base_priors * 4

    def test_loss_by_feat_single(self, rtmdet_head) -> None:
        # Create dummy data to simulate the inputs to the loss_by_feat_single method
        cls_score = torch.rand(1, 2, 100, 80)
        bbox_pred = torch.rand(1, 2, 100, 4)
        labels = torch.randint(0, 80, (2, 100))
        label_weights = torch.rand(2, 100)
        bbox_targets = torch.rand(2, 100, 4)
        assign_metrics = torch.rand(2, 100)
        stride = [8, 8]

        loss_cls, loss_bbox, _, _ = rtmdet_head.loss_by_feat_single(
            cls_score,
            bbox_pred,
            labels,
            label_weights,
            bbox_targets,
            assign_metrics,
            stride,
        )

        assert loss_cls is not None
        assert loss_bbox is not None

    def test_get_anchors(self, rtmdet_head) -> None:
        featmap_sizes = [(40, 40), (20, 20), (10, 10)]
        batch_img_metas = [{"img_shape": (320, 320, 3)} for _ in range(2)]
        device = "cpu"

        anchor_list, valid_flag_list = rtmdet_head.get_anchors(featmap_sizes, batch_img_metas, device=device)

        assert len(anchor_list) == len(batch_img_metas)
        assert len(valid_flag_list) == len(batch_img_metas)
        for anchors, valid_flags in zip(anchor_list, valid_flag_list):
            assert len(anchors) == len(featmap_sizes)
            assert len(valid_flags) == len(featmap_sizes)
            for anchor, valid_flag in zip(anchors, valid_flags):
                assert anchor.shape[1] == 4
                assert valid_flag.dtype == torch.bool


class TestRTMDetSepBNHead:
    @pytest.fixture()
    def rtmdet_sep_bn_head(self) -> RTMDetSepBNHead:
        train_cfg = {
            "assigner": DynamicSoftLabelAssigner(topk=13),
            "sampler": PseudoSampler(),
            "allowed_border": -1,
            "pos_weight": -1,
            "debug": False,
        }

        test_cfg = DictConfig(
            {
                "nms": {"type": "nms", "iou_threshold": 0.65},
                "score_thr": 0.001,
                "mask_thr_binary": 0.5,
                "max_per_img": 300,
                "min_bbox_size": 0,
                "nms_pre": 30000,
            },
        )
        return RTMDetSepBNHead(
            num_classes=80,
            in_channels=96,
            stacked_convs=2,
            feat_channels=96,
            anchor_generator=MlvlPointGenerator(offset=0, strides=[8, 16, 32]),
            bbox_coder=DistancePointBBoxCoder(),
            loss_cls=QualityFocalLoss(use_sigmoid=True, beta=2.0, loss_weight=1.0),
            loss_bbox=GIoULoss(loss_weight=2.0),
            with_objectness=False,
            exp_on_reg=False,
            share_conv=True,
            pred_kernel_size=1,
            norm_cfg={"type": "BN"},
            act_cfg={"type": "SiLU", "inplace": True},
            train_cfg=train_cfg,
            test_cfg=test_cfg,
        )

    def test_rtmdet_sep_bn_head_forward(self, rtmdet_sep_bn_head, input_features) -> None:
        cls_scores, bbox_preds = rtmdet_sep_bn_head(input_features)
        assert len(cls_scores) == len(input_features)
        assert len(bbox_preds) == len(input_features)
        for cls_score, bbox_pred in zip(cls_scores, bbox_preds):
            # The number of channels in cls_scores should be num_base_priors * num_classes
            assert cls_score.size(1) == rtmdet_sep_bn_head.num_base_priors * rtmdet_sep_bn_head.cls_out_channels
            # The number of channels in bbox_preds should be num_base_priors * 4
            assert bbox_pred.size(1) == rtmdet_sep_bn_head.num_base_priors * 4
