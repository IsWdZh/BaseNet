import torch
import torch.nn as nn
from torch.autograd import Function
from string import Template
import cupy
from collections import namedtuple
from model.roi_cupy_config import kernel_forward, kernel_backward


Stream = namedtuple('Stream', ['ptr'])

def load_kernel(kernel_name, code, **kwargs):
    cupy.cuda.runtime.free(0)
    code = Template(code).substitute(**kwargs)
    kernel_code = cupy.cuda.compile_with_cache(code)
    return kernel_code.get_function(kernel_name)


CUDA_NUM_THREADS = 1024    # 线程数

def GET_BLOCKS(N, K=CUDA_NUM_THREADS):
    return (N + K - 1) // K

class RoI(Function):
    def __init__(self, pooled_height, pooled_width, spatial_scale):
        self.forward_fn = load_kernel('roi_forward', kernel_forward)
        self.backward_fn = load_kernel('roi_backward', kernel_backward)
        self.pooled_height, self.pooled_width, self.spatial_scale = \
            pooled_height, pooled_width, spatial_scale

    def forward(self, features, rois):
        # NOTE: MAKE SURE input is contiguous too
        features = features.contiguous()
        rois = rois.contiguous()
        self.in_size = B, C, H, W = features.size()
        self.N = N = rois.size(0)
        output = torch.zeros(N, C, self.pooled_height, self.pooled_width).cuda()
        self.argmax_data = torch.zeros(N, C, self.pooled_height, self.pooled_width).int().cuda()
        self.rois = rois
        args = [features.data_ptr(), rois.data_ptr(),
                output.data_ptr(),
                self.argmax_data.data_ptr(),
                self.spatial_scale, C, H, W,
                self.pooled_height, self.pooled_width,
                output.numel()]
        stream = Stream(ptr=torch.cuda.current_stream().cuda_stream)
        self.forward_fn(args=args,
                        block=(CUDA_NUM_THREADS, 1, 1),
                        grid=(GET_BLOCKS(output.numel()), 1, 1),
                        stream=stream)
        return output

    def backward(self, grad_output):
        ##NOTE: IMPORTANT CONTIGUOUS
        grad_output = grad_output.contiguous()
        B, C, H, W = self.in_size
        grad_input = torch.zeros(self.in_size).cuda()
        stream = Stream(ptr=torch.cuda.current_stream().cuda_stream)
        args = [grad_output.data_ptr(),
                self.argmax_data.data_ptr(),
                self.rois.data_ptr(),
                grad_input.data_ptr(),
                self.N, self.spatial_scale, C, H, W, self.pooled_height, self.pooled_width,
                grad_input.numel()]
        self.backward_fn(args=args,
                         block=(CUDA_NUM_THREADS, 1, 1),
                         grid=(GET_BLOCKS(grad_input.numel()), 1, 1),
                         stream=stream
                         )
        return grad_input, None


class _RoIPooling2d(nn.Module):
    def __init__(self, pooled_height, pooled_width, spatial_scale):
        super(_RoIPooling2d, self).__init__()
        self.RoI = RoI(pooled_height, pooled_width, spatial_scale)

    def forward(self, features, rois):
        return self.RoI(features, rois)




def test_roi_module():
    ## fake data###
    B, N, C, H, W, PH, PW = 2, 8, 4, 32, 32, 7, 7

    bottom_data = torch.randn(B, C, H, W).cuda()
    bottom_rois = torch.randn(N, 5)
    bottom_rois[:int(N / 2), 0] = 0
    bottom_rois[int(N / 2):, 0] = 1
    bottom_rois[:, 1:] = (torch.rand(N, 4) * 100).float()
    bottom_rois = bottom_rois.cuda()
    spatial_scale = 1. / 16
    outh, outw = PH, PW

    # pytorch version
    module = _RoIPooling2d(outh, outw, spatial_scale)
    x = bottom_data.requires_grad_()
    rois = bottom_rois.detach()

    output = module(x, rois)
    output.sum().backward()

    def t2c(variable):
        npa = variable.data.cpu().numpy()
        return cupy.array(npa)

    def test_eq(variable, array, info):
        cc = cupy.asnumpy(array)
        neq = (cc != variable.data.cpu().numpy())
        assert neq.sum() == 0, 'test failed: %s' % info

    # chainer version,if you're going to run this
    # pip install chainer
    import chainer.functions as F
    from chainer import Variable
    import numpy as np
    from IPython import embed
    x_cn = Variable(t2c(x))

    o_cn = F.roi_pooling_2d(x_cn, t2c(rois), outh, outw, spatial_scale)
    # print(type(o_cn))
    print("\n-------------------\n")
    print(o_cn.data)
    print(type(o_cn.data.size))
    # print(torch.from_numpy(o_cn.data))
    test_eq(output, o_cn.array, 'forward')
    F.sum(o_cn).backward()
    test_eq(x.grad, x_cn.grad, 'backward')
    print('test pass')


'''
if __name__=="__main__":
    test_roi_module()
'''