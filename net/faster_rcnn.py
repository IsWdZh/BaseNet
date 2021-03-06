import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F
import numpy as np
from model.rpn.rpn import _RPN
from model.rpn.proposal_target_layer import _ProposalTargetLayer
from model.roi_pooling.roipooling import RoIPooling2D, RoIPooling2D_chainer
from logger import get_logger


logger = get_logger()
class Faster_RCNN(nn.Module):
    '''Faster_RCNN model'''
    def __init__(self, classes):
        super(Faster_RCNN, self).__init__()
        self.classes = classes
        self.num_classes = len(classes)
        self.RCNN_loss_cls = 0
        self.RCNN_loss_bbox = 0

        # define rpn
        self.RCNN_rpn = _RPN(self.base_feat_out_dim, use_gpu=self.use_gpu)
        self.RCNN_proposal_target = _ProposalTargetLayer(self.num_classes)

        # size of the pooled region after ROI pooling.
        self.RCNN_roi_pool = RoIPooling2D(7, 7, 1.0/16.0)
        self.RCNN_roi_pool_chainer = RoIPooling2D_chainer(7, 7, 1.0/16.0)

    def forward(self, im_data, im_info, gt_boxes, num_boxes):
        self.batch_size = im_data.size(0)

        im_info = im_info.data
        gt_boxes = gt_boxes.data
        num_boxes = num_boxes.data
        logger.debug("im_data = {}, im_info = {}, gt_boxes = {}, num_boxes = {}\n".format(im_data.size(),
                                                                                   im_info.size(),
                                                                                   gt_boxes.size(),
                                                                                   num_boxes.size()))
        # feed image data to base model to obtain base feature map
        base_feat = self.RCNN_base(im_data)
        logger.debug("base_feat = {}\n".format(base_feat.size()))

        rois, rpn_loss_cls, rpn_loss_bbox = self.RCNN_rpn(base_feat, im_info, gt_boxes, num_boxes)
        logger.debug("After RPN layer, rois={}, rpn_loss_cls={}, rpn_loss_bbox={}".format(rois.size(),
                                                                                   rpn_loss_cls.size(),
                                                                                   rpn_loss_bbox.size()))
        logger.debug("rpn_loss_cls = {}, rpn_loss_bbox = {}".format(rpn_loss_cls, rpn_loss_bbox))

        if self.training:
            roi_data = self.RCNN_proposal_target(rois, gt_boxes, num_boxes, self.batch_size)
            rois, rois_label, rois_target, rois_inside_ws, rois_outside_ws = roi_data
            logger.debug("In faster_rcnn: rois = {}, rois_label = {}, rois_target = {}, "
                  "rois_inside_ws = {}, rois_outside_ws = {}".format(rois.size(), rois_label.size(),
                                                                     rois_target.size(), rois_inside_ws.size(),
                                                                     rois_outside_ws.size()))

            rois_label = Variable(rois_label.view(-1).long())
            rois_target = Variable(rois_target.view(-1, rois_target.size(2)))
            rois_inside_ws = Variable(rois_inside_ws.view(-1, rois_inside_ws.size(2)))
            rois_outside_ws = Variable(rois_outside_ws.view(-1, rois_outside_ws.size(2)))
        else:
            rois_label = None
            rois_target = None
            rois_inside_ws = None
            rois_outside_ws = None
            rpn_loss_cls = 0
            rpn_loss_bbox = 0

        rois = Variable(rois)
        logger.debug("In faster_rcnn: Before RCNN_roi_pool, base_feat = {}, "
              "rois.view(-1,5) = {}".format(base_feat.size(), rois.view(-1,5).size()))
        # roi_pic = pic[:, rois.view(-1,5)[0][0]:rois.view(-1,5)[0][2],
        #           rois.view(-1,5)[0][1]:rois.view(-1,5)[0][3]]

        # rois:[1,1,5]  -->  rois.view(-1,5):[1,5]
        # pooled_feat = self.RCNN_roi_pool(base_feat, rois.view(-1, 5))
        pooled_feat = self.RCNN_roi_pool_chainer(base_feat, rois.view(-1, 5))
        if self.use_gpu:
            pooled_feat = Variable(pooled_feat).cuda()
        logger.debug("faster_cnn -> pooled_feat = {}".format(pooled_feat.size()))

        # 展开
        pooled_feat_flat = pooled_feat.view(pooled_feat.size(0), -1)
        pooled_feat_fc = self.RCNN_top(pooled_feat_flat)

        bbox_pred = self.RCNN_bbox_pred(pooled_feat_fc)
        logger.debug("faster_cnn -> bbox_pred = {}".format(bbox_pred.size()))

        if self.training:
            # select the corresponding columns according to roi labels
            # [1,84] -> [1,21,4]
            bbox_pred_view = bbox_pred.view(bbox_pred.size(0), int(bbox_pred.size(1) / 4), 4)
            logger.debug("faster_rcnn -> bbox_pred_view = {}".format(bbox_pred_view.size()))

            # bbox_pred_select = torch.Size([1, 1, 4])
            bbox_pred_select = torch.gather(bbox_pred_view, 1,
                                            rois_label.view(rois_label.size(0), 1, 1).expand(rois_label.size(0), 1, 4))
            logger.debug("faster_rcnn -> bbox_pred_select = {}".format(bbox_pred_select.size()))

            # bbox_pred = torch.Size([1, 4])
            bbox_pred = bbox_pred_select.squeeze(1)
            logger.debug("faster_rcnn -> bbox_pred = {}".format(bbox_pred.size()))

            # compute object classification probability
            cls_score = self.RCNN_cls_score(pooled_feat_fc)
            logger.debug("faster_rcnn -> cls_score = {}".format(cls_score.size()))
            cls_prob = F.softmax(cls_score, dim=1)
            logger.debug("faster_rcnn -> cls_prob = {}".format(cls_prob.size()))

            RCNN_loss_cls = 0
            RCNN_loss_bbox = 0

            if self.training:
                # classification loss
                RCNN_loss_cls = F.cross_entropy(cls_score, rois_label)
                logger.debug("faster_rcnn -> RCNN_loss_cls = {}, size={}".format(RCNN_loss_cls,
                                                                          RCNN_loss_cls.size()))

                # bounding box regression L1 loss
                RCNN_loss_bbox = self._smooth_l1_loss(bbox_pred, rois_target,
                                                      rois_inside_ws, rois_outside_ws)
                logger.debug("faster_rcnn -> RCNN_loss_bbox = {}, size={}".format(RCNN_loss_bbox,
                                                                           RCNN_loss_bbox.size()))


            cls_prob = cls_prob.view(self.batch_size, rois.size(1), -1)
            bbox_pred = bbox_pred.view(self.batch_size, rois.size(1), -1)

            return rois, cls_prob, bbox_pred, rpn_loss_cls, \
                   rpn_loss_bbox, RCNN_loss_cls, RCNN_loss_bbox, rois_label

    def _init_weights(self):
        def normal_init(m, mean, stddev, truncated=False):
            """
            权重初始化: 截断正态分布/随机正态分布
            """
            if truncated:
                m.weight.data.normal_().fmod_(2).mul_(stddev).add_(mean) # not a perfect approximation
            else:
                m.weight.data.normal_(mean, stddev)
                m.bias.data.zero_()

        normal_init(self.RCNN_rpn.RPN_Conv, 0, 0.01)
        normal_init(self.RCNN_rpn.RPN_cls_score, 0, 0.01)
        normal_init(self.RCNN_rpn.RPN_bbox_pred, 0, 0.01)
        normal_init(self.RCNN_cls_score, 0, 0.01)
        normal_init(self.RCNN_bbox_pred, 0, 0.001)


    def init_model(self):
        self._init_modules()
        self._init_weights()


    def _smooth_l1_loss(self, bbox_pred, bbox_targets, bbox_inside_weights,
                        bbox_outside_weights, sigma=1.0, dim=[1]):

        sigma_2 = sigma ** 2
        box_diff = bbox_pred - bbox_targets
        in_box_diff = bbox_inside_weights * box_diff
        abs_in_box_diff = torch.abs(in_box_diff)
        smoothL1_sign = (abs_in_box_diff < 1. / sigma_2).detach().float()
        in_loss_box = torch.pow(in_box_diff, 2) * (sigma_2 / 2.) * smoothL1_sign \
                      + (abs_in_box_diff - (0.5 / sigma_2)) * (1. - smoothL1_sign)
        out_loss_box = bbox_outside_weights * in_loss_box
        loss_box = out_loss_box
        for i in sorted(dim, reverse=True):
            loss_box = loss_box.sum(i)
        loss_box = loss_box.mean()
        return loss_box

    def clip_gradient(self, model, clip_norm):
        """Computes a gradient clipping coefficient based on gradient norm."""
        if self.use_gpu:
            model = model.cpu()

        totalnorm = 0
        for p in model.parameters():
            if p.requires_grad:
                modulenorm = p.grad.data.norm()
                totalnorm += modulenorm ** 2
        totalnorm = np.sqrt(totalnorm)

        norm = clip_norm / max(totalnorm, clip_norm)
        for p in model.parameters():
            if p.requires_grad:
                p.grad.mul_(norm)

        if self.use_gpu:
            model = model.cuda()
        

