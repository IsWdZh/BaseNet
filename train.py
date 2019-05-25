import torch
import torch.nn as nn
from torch.utils.data.sampler import Sampler
import torch.optim as optim
from torch.autograd import Variable
from data.roidb import combined_roidb
from data.roi_batch_load import roibatchLoader
from data.sampler import sampler
from net.vgg import VGG16
from IPython import embed
import time
from logger import Logger
import tqdm
import os
import PIL.Image as Image

max_iter = 500
epoch_save = 500
batch_size = 1
lr = 0.001
lr_decay_step = 50     # step to do lr decay (epoch)
lr_decay_gamma = 0.5    # learning rate decay ratio
momentum = 0.9
weight_decay = 0.0005
display_iter_num = 1    # iterator display num
USE_WEIGHT_DECAY_ON_BIAS = False
DOUBLE_LR_ON_BIAS = True


# now_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))

output_path = os.path.join(os.getcwd(), "output")
if not os.path.exists(output_path):
    os.makedirs(output_path)
logger = Logger()


imdb_name = "voc_2007_trainval"
imdb, roidb, ratio_list, ratio_index = combined_roidb(imdb_name)
train_size = len(roidb)
logger.info("Classes: {}".format(imdb.classes))

exit()
sampler_batch = sampler(train_size, batch_size)
dataset = roibatchLoader(roidb, ratio_list, ratio_index, batch_size,
                         imdb.num_classes, training=True)
dataloader = torch.utils.data.DataLoader(dataset, batch_size=batch_size,
                        sampler=sampler_batch, num_workers=4)

# im_data = Variable(torch.FloatTensor(1).cuda())
im_data = Variable(torch.FloatTensor(1))
im_info = Variable(torch.FloatTensor(1))
num_boxes = Variable(torch.LongTensor(1))
gt_boxes = Variable(torch.FloatTensor(1))

data_iter = iter(dataloader)
data = next(data_iter)

im_data.data.resize_(data[0].size()).copy_(data[0])
im_info.data.resize_(data[1].size()).copy_(data[1])
gt_boxes.data.resize_(data[2].size()).copy_(data[2])
num_boxes.data.resize_(data[3].size()).copy_(data[3])

faster_rcnn = VGG16(imdb.classes)
faster_rcnn.init_model()
logger.info(faster_rcnn)
exit()

optimizer = torch.optim.SGD(faster_rcnn.parameters(), lr=lr, momentum=momentum,
                            weight_decay=weight_decay)

faster_rcnn.forward(im_data, im_info, gt_boxes, num_boxes)

params = []
for key, value in dict(faster_rcnn.named_parameters()).items():
    if value.requires_grad:
        if 'bias' in key:
            params += [{'params':[value], 'lr':lr*(DOUBLE_LR_ON_BIAS + 1),
                        'weight_decay': USE_WEIGHT_DECAY_ON_BIAS and weight_decay or 0}]
        else:
            params += [{'params':[value], 'lr':lr, 'weight_decay': weight_decay}]

optimizer = torch.optim.SGD(params, momentum=0.9)

iters_per_epoch = int(train_size/batch_size)
for epoch in range(1, max_iter+1):
    faster_rcnn.train()
    loss_temp = 0
    start = time.time()

    if epoch % lr_decay_step == 0:
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr_decay_gamma * param_group['lr']
        lr *= lr_decay_gamma

    # data_iter = iter(dataloader)
    # for step in range(iters_per_epoch)
    #     data = next(data_iter)

    bar = tqdm.tqdm(dataloader, total=len(dataloader))
    for step, data in enumerate(bar):
        step += 1
        im_data.data.resize_(data[0].size()).copy_(data[0])
        im_info.data.resize_(data[1].size()).copy_(data[1])
        gt_boxes.data.resize_(data[2].size()).copy_(data[2])
        num_boxes.data.resize_(data[3].size()).copy_(data[3])

        faster_rcnn.zero_grad()

        rois, cls_prob, bbox_pred, rpn_loss_cls, rpn_loss_box, \
        RCNN_loss_cls, RCNN_loss_bbox, rois_label \
            = faster_rcnn(im_data, im_info, gt_boxes, num_boxes)
        print("train -> ")
        print("rois={}, cls_prob={}, bbox_pred={}, "
              "rpn_loss_cls={}".format(rois.size(), cls_prob.size(),
                                       bbox_pred.size(), rpn_loss_cls.size()))
        print("rpn_loss_box={}, RCNN_loss_cls={}, \n"
              "RCNN_loss_bbox={}, rois_label={}".format(rpn_loss_box, RCNN_loss_cls,
                                                        RCNN_loss_bbox, rois_label))
        print(type(rpn_loss_box))

        loss = rpn_loss_cls.mean() + rpn_loss_box.mean() + \
               RCNN_loss_cls.mean() + RCNN_loss_bbox.mean()
        print("train -> loss = {}".format(loss.size()))

        # loss_temp += loss.data[0]
        loss_temp += loss.item()

        optimizer.zero_grad()
        loss.backward()

        faster_rcnn.clip_gradient(faster_rcnn, 10.)
        optimizer.step()

        if step % display_iter_num == 0:
            end = time.time()
            if step > 0:
                loss_temp /= (display_iter_num + 1)
            # loss_rpn_cls = rpn_loss_cls.data[0]
            # loss_rpn_box = rpn_loss_box.data[0]
            # loss_rcnn_cls = RCNN_loss_cls.data[0]
            # loss_rcnn_box = RCNN_loss_bbox.data[0]
            loss_rpn_cls = rpn_loss_cls.item()
            loss_rpn_box = rpn_loss_box.item()
            loss_rcnn_cls = RCNN_loss_cls.item()
            loss_rcnn_box = RCNN_loss_bbox.item()

            fg_cnt = torch.sum(rois_label.data.ne(0))
            bg_cnt = rois_label.data.numel() - fg_cnt
            bar.set_description("epoch{:2d} lr:{:.2e} loss:{:.4f} "
                                ":rpn_cls:{:.4f},rpn_box:{:.4f}, "
                                "rcnn_cls:{:.4f},rcnn_box{:.4f}".format(epoch, lr,
                                                                        loss_temp,
                                                                        loss_rpn_cls,
                                                                        loss_rpn_box,
                                                                        loss_rcnn_cls,
                                                                        loss_rcnn_box))

            loss_temp = 0
            # start = time.time()

    if epoch % epoch_save == 0:
        save_name = os.path.join(output_path,
                                 'faster_rcnn_{}_{}.pth'.format(epoch, step))
        torch.save({
            'epoch': epoch + 1,
            'model': faster_rcnn.state_dict(),
            'optimizer': optimizer.state_dict()
        }, save_name)
        print('save model: {}'.format(save_name))

        end = time.time()
        print(end - start)




