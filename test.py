import chainer
import os
import torch
import torch.nn.functional as functional

out = os.path.join(os.getcwd(), "out")
print(os.getcwd())
print(os.path.abspath(__file__))
print(os.path.exists(out))
chainer.functions.roi_pooling_2d()
functional.adaptive_max_pool2d()


# chainer.functions.roi_pooling_2d()