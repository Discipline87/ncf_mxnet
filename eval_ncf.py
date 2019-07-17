# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# 
import os
import time
import argparse
import logging
import numpy as np
import mxnet as mx
from Dataset import Dataset
from model import get_model
from evaluate import evaluate_model

logging.basicConfig(level=logging.DEBUG)

parser = argparse.ArgumentParser(description="Run matrix factorization with embedding",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--path', nargs='?', default='./data/',
                    help='Input data path.')
parser.add_argument('--dataset', nargs='?', default='ml-20m',
                    help='The dataset name.')
parser.add_argument('--batch-size', type=int, default=256,
                    help='number of examples per batch')
parser.add_argument('--model-type', type=str, default='neumf', choices=['neumf', 'gmf', 'mlp'],
                    help="mdoel type")
parser.add_argument('--layers', default='[256, 128, 64]',
                    help="list of number hiddens of fc layers in mlp model.")
parser.add_argument('--factor-size-gmf', type=int, default=64,
                    help="outdim of gmf embedding layers.")
parser.add_argument('--num-hidden', type=int, default=1,
                    help="num-hidden of neumf fc layer")
parser.add_argument('--gpus', type=str,
                    help="list of gpus to run, e.g. 0 or 0,2. empty means using cpu().")
parser.add_argument('--sparse', action='store_true', help="whether to use sparse embedding")
parser.add_argument('--evaluate', action='store_true', help="whether to evaluate accuracy")
parser.add_argument('--epoch', type=int, default=0, help='model checkpoint index for inference')
parser.add_argument('--deploy', action='store_true', help="whether to load static graph for deployment")
parser.add_argument('--prefix', default='checkpoint', help="mdoel prefix for deployment")

def get_movielens_iter(filename, batch_size, logger):
    """Not particularly fast code to parse the text file and load into NDArrays.
    return two data iters, one for train, the other for validation.
    """
    logger.info("Preparing data iterators for " + filename + " ... ")
    user = []
    item = []
    score = []
    with open(filename, 'r') as f:
        num_samples = 0
        for line in f:
            tks = line.strip().split('\t')
            if len(tks) != 3:
                continue
            num_samples += 1
            user.append((tks[0]))
            item.append((tks[1]))
            score.append((tks[2]))
    # convert to ndarrays
    user = mx.nd.array(user, dtype='int32')
    item = mx.nd.array(item)
    score = mx.nd.array(score)
    # prepare data iters
    data = {'user': user, 'item': item}
    label = {'softmax_label': score}
    iter = mx.io.NDArrayIter(data=data, label=label, batch_size=batch_size)
    return iter

if __name__ == '__main__':
    head = '%(asctime)-15s %(message)s'
    logging.basicConfig(level=logging.INFO, format=head)

    # arg parser
    args = parser.parse_args()
    logging.info(args)

    batch_size = args.batch_size
    model_type = args.model_type
    model_layers = eval(args.layers)
    factor_size_gmf = args.factor_size_gmf
    factor_size_mlp = int(model_layers[0]/2)
    num_hidden = args.num_hidden
    sparse = args.sparse
    ctx = [mx.gpu(int(i)) for i in args.gpus.split(',')] if args.gpus else mx.cpu()
    topK = 10
    evaluation_threads = 1#mp.cpu_count()

    # prepare dataset and iterators
    logging.info('Prepare Dataset')
    if not args.deploy and args.evaluate:
        data = Dataset(args.path + args.dataset)
        train, testRatings, testNegatives = data.trainMatrix, data.testRatings, data.testNegatives
        max_user, max_movies = train.shape
        logging.info("Load validation data done. #user=%d, #item=%d, #test=%d" 
                    %(max_user, max_movies, len(testRatings)))
    val_iter = get_movielens_iter(args.path + args.dataset + '.train.rating', batch_size, logger=logging)
    logging.info('Prepare Dataset completed')
    # construct the model
    if args.deploy:
        net, arg_params, aux_params = mx.model.load_checkpoint(args.prefix, args.epoch)
    else:
        net = get_model(model_type, factor_size_mlp, factor_size_gmf, 
                        model_layers, num_hidden, max_user, max_movies, sparse)
        dir_path = os.path.dirname(os.path.realpath(__file__))
        model_path = os.path.join(dir_path, 'model', args.dataset)
        save_dict = mx.nd.load(os.path.join(model_path, model_type) + "-%04d.params" % args.epoch)
        arg_params = {}
        aux_params = {}
        for k, v in save_dict.items():
            tp, name = k.split(':', 1)
            if tp == 'arg':
                arg_params[name] = v
            if tp == 'aux':
                aux_params[name] = v
    if ctx == mx.cpu():
        net = net.get_backend_symbol("MKLDNN")

    # initialize the module
    mod = mx.module.Module(net, context=ctx, data_names=['user', 'item'], label_names=['softmax_label'])
    mod.bind(for_training=False, data_shapes=val_iter.provide_data, label_shapes=val_iter.provide_label)
    mod.set_params(arg_params, aux_params)

    if args.evaluate:
        (hits, ndcgs) = evaluate_model(mod, testRatings, testNegatives, topK, evaluation_threads)
        hr, ndcg = np.array(hits).mean(), np.array(ndcgs).mean()
        logging.info('Evaluate: HR = %.4f, NDCG = %.4f'  % (hr, ndcg))
    else:
        logging.info('Inference...')
        tic = time.time()
        num_samples = 0
        for batch in val_iter:
            mod.forward(batch, is_train=False)
            mx.nd.waitall()
            num_samples += batch_size
        toc = time.time()
        fps = num_samples/(toc - tic)
        logging.info('Evaluating completed')
        logging.info('Inference speed %.4f fps' % fps)
