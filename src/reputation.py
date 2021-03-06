import time
import math
import random

# from tqdm import tqdm

import torch


def by_random(
        proposals: list, count: int,
        return_acc=False, test_client=None, epoch=None, show=False, log=False,
        timing=False):

    if timing:
        start = time.time()

    n = len(proposals)
    assert(n >= count)

    elapsed = None
    accs = []

    idxes = random.sample(range(n), count)

    if return_acc and (test_client is not None) and (epoch is not None):
        for idx in idxes:
            test_client.set_weights(proposals[idx].get_weights())
            res = 100. - test_client.test(epoch, show=show, log=log)
            accs.append(res)

    # elapsed time
    if timing:
        elapsed = time.time() - start
        # print(elapsed)

    return accs, idxes, elapsed


def suffle(A):
    return (list(t) for t in zip(*(random.sample([i for i in (enumerate(A))], len(A)))))


def by_accuracy(
        proposals: list, count: int, test_client,
        epoch, show=False, log=False,
        timing=False, optimal_stopping=False):

    if timing:
        start = time.time()

    n = len(proposals)
    assert(n >= count)

    bests, idx_bests, elapsed = [], [], None
    accs = []

    if optimal_stopping and (n >= 3):
        """optimal stopping mode
        # TODO: Randomize input list (proposals)
        # TODO: not a best, but t% satisfaction (10, 20, ...)
        # Ref. this: https://horizon.kias.re.kr/6053/
        """
        passing_number = int(n / math.e)
        cutline = 0.

        idx_suffled, suffled = suffle(proposals)

        for i, proposal in enumerate(suffled):
            test_client.set_weights(proposal.get_weights())
            res = 100. - test_client.test(epoch, show=show, log=log)
            accs.append(res)
            idx_bests.append(idx_suffled[i])
            if cutline < res:
                cutline = res
                if (i >= passing_number) and (i + 1 >= count):
                    break
    else:
        """normal mode
        # TBA
        """
        for i, proposal in enumerate(proposals):  # tqdm(proposals):
            test_client.set_weights(proposal.get_weights())
            res = 100. - test_client.test(epoch, show=show, log=log)
            accs.append(res)
            idx_bests.append(i)

    # print(accs)
    bests = accs[:]
    bests, idx_bests = (list(t)[:count] for t in zip(*sorted(zip(bests, idx_bests), reverse=True)))

    # elapsed time
    if timing:
        elapsed = time.time() - start
        # print(elapsed)

    return bests, idx_bests, elapsed


def filterwise_normalization(weights: dict):
    theta = Frobenius(weights)

    res = dict()
    for name, value in weights.items():
        d = Frobenius({name: value})
        d += 1e-10  # Ref. https://github.com/tomgoldstein/loss-landscape/blob/master/net_plotter.py#L111
        res[name] = value.div(d).mul(theta)

    return res


def Frobenius(weights: dict, base_weights: dict = None):
    total = 0.
    for name, value in weights.items():
        if base_weights is not None:
            elem = value.sub(base_weights[name])
        else:
            elem = value.clone().detach()

        elem.mul_(elem)
        total += torch.sum(elem).item()

    return math.sqrt(total)


def by_Frobenius(
        proposals: list, count: int, base_client, FN=False,
        return_acc=False, test_client=None, epoch=None, show=False, log=False,
        timing=False, optimal_stopping=False):

    if timing:
        start = time.time()

    n = len(proposals)
    assert(n >= count)

    bests, idx_bests, elapsed = [], [], None
    distances = []

    if optimal_stopping and (n >= 3):
        """optimal stopping mode
        # TODO: Her own weights' Frobenius Norm is 0
        # so they are always best.
        """
        passing_number = int(n / math.e)
        cutline = 0.

        idx_suffled, suffled = suffle(proposals)
        cached = None

        for i, proposal in enumerate(suffled):  # enumerate(tqdm(proposals)):
            if FN:
                if cached is None:
                    cached = filterwise_normalization(base_client.get_weights())

                res = -1 * Frobenius(
                    filterwise_normalization(proposal.get_weights()),
                    base_weights=cached)
            else:
                res = -1 * Frobenius(
                    proposal.get_weights(), base_weights=base_client.get_weights())

            if i == 0:
                cutline = res

            distances.append(res)
            idx_bests.append(idx_suffled[i])

            if cutline < res:
                cutline = res
                if i >= passing_number and (i + 1 >= count):
                    break
    else:
        """normal mode
        # TBA
        """
        cached = None

        for i, proposal in enumerate(proposals):
            if FN:
                if cached is None:
                    cached = filterwise_normalization(base_client.get_weights())

                res = -1 * Frobenius(
                    filterwise_normalization(proposal.get_weights()),
                    base_weights=cached)
            else:
                res = -1 * Frobenius(
                    proposal.get_weights(), base_weights=base_client.get_weights())

            distances.append(res)
            idx_bests.append(i)

    # print(distances)
    bests = distances[:]
    bests, idx_bests = (list(t)[:count] for t in zip(*sorted(zip(bests, idx_bests), reverse=True)))
    bests = [-1 * b for b in bests]

    if return_acc and (test_client is not None) and (epoch is not None):
        accs = []
        for idx_best in idx_bests:
            test_client.set_weights(proposals[idx_best].get_weights())
            res = 100. - test_client.test(epoch, show=show, log=log)
            accs.append(res)
        bests = accs[:]

    # elapsed time
    if timing:
        elapsed = time.time() - start
        # print(elapsed)

    return bests, idx_bests, elapsed


def by_GNN():
    pass  # TODO


def by_population():
    pass  # TODO: NAS, ES


if __name__ == "__main__":
    # python src/reputation.py --nNodes=40 --nPick=10 --nEpochs=10 --load
    # python src/reputation.py --nNodes=10 --nPick=2 --load

    import argparse

    import torchvision.datasets as dset
    import torchvision.transforms as transforms
    from torch.utils.data import random_split

    from net import DenseNet
    from client import Client

    """argparse"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--nNodes', type=int, default=100)
    parser.add_argument('--nPick', type=int, default=5)
    parser.add_argument('--batchSz', type=int, default=128)
    parser.add_argument('--nEpochs', type=int, default=300)
    parser.add_argument('--nLoops', type=int, default=100)
    parser.add_argument('--no-cuda', action='store_true')
    parser.add_argument('--load', action='store_true')
    parser.add_argument('--path')
    parser.add_argument('--seed', type=int, default=950327)
    parser.add_argument('--opt', type=str, default='sgd',
                        choices=('sgd', 'adam', 'rmsprop'))
    args = parser.parse_args()

    args.cuda = not args.no_cuda and torch.cuda.is_available()

    # set seed
    torch.manual_seed(args.seed)
    if args.cuda:
        torch.cuda.manual_seed(args.seed)

    """Data
    # TODO: get Mean and Std per client
    # Ref: https://github.com/bamos/densenet.pytorch
    """
    normMean = [0.49139968, 0.48215827, 0.44653124]
    normStd = [0.24703233, 0.24348505, 0.26158768]
    normTransform = transforms.Normalize(normMean, normStd)

    trainTransform = transforms.Compose([
        transforms.RandomCrop(32, padding=4),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        normTransform
    ])
    testTransform = transforms.Compose([
        transforms.ToTensor(),
        normTransform
    ])

    trainset = dset.CIFAR10(root='cifar', train=True, download=True, transform=trainTransform)
    testset = dset.CIFAR10(root='cifar', train=False, download=True, transform=testTransform)

    # Random split
    splited_trainset = random_split(trainset, [int(len(trainset) / args.nNodes) for _ in range(args.nNodes)])
    splited_testset = random_split(testset, [int(len(testset) / args.nNodes) for _ in range(args.nNodes)])

    """FL
    # TBA
    """
    def _dense_net():
        return DenseNet(growthRate=12, depth=100, reduction=0.5, bottleneck=True, nClasses=10)
        # print('>>> Number of params: {}'.format(
        #     sum([p.data.nelement() for p in net.parameters()])))

    tmp_client = Client(  # for eval. the others' net / et al.
        args=args,
        net=_dense_net(),
        trainset=None,
        testset=None,
        log=False,
        _id=-1)

    clients = []
    for i in range(args.nNodes):
        client = Client(
            args=args,
            net=_dense_net(),
            trainset=splited_trainset[i],
            testset=splited_testset[i],
            log=True and (not args.load))
        client.set_weights(tmp_client.get_weights())
        clients.append(client)

    if args.load:
        for i in range(args.nNodes):
            clients[i].load()
    else:
        for c in range(args.nNodes):
            for i in range(1, args.nEpochs + 1):
                clients[c].train(epoch=i, show=True)
            clients[c].save()

    eta = dict()

    for r in range(1, args.nLoops + 1):

        for c in range(args.nNodes):
            print("\n")
            print("Round", r, end='\t')
            print("Client", c)

            tmp_client.set_dataset(trainset=None, testset=clients[c].testset)

            # by accuracy
            bests, idx_bests, elapsed = by_accuracy(
                proposals=clients, count=args.nPick, test_client=tmp_client,
                epoch=r, show=False, log=False,
                timing=True, optimal_stopping=False)
            print("Acc\t:", idx_bests, elapsed)
            if 'acc' not in eta:
                eta['acc'] = []
            eta['acc'].append(elapsed)

            # by accuracy with optimal stopping
            bests, idx_bests, elapsed = by_accuracy(
                proposals=clients, count=args.nPick, test_client=tmp_client,
                epoch=r, show=False, log=False,
                timing=True, optimal_stopping=True)
            print("Acc(OS)\t:", idx_bests, elapsed)
            if 'acc_os' not in eta:
                eta['acc_os'] = []
            eta['acc_os'].append(elapsed)

            # by Frobenius L2 norm
            bests, idx_bests, elapsed = by_Frobenius(
                proposals=clients, count=args.nPick, base_client=clients[c], FN=False,
                return_acc=True, test_client=tmp_client, epoch=r, show=False, log=False,
                timing=True, optimal_stopping=False)
            print("F\t:", idx_bests, elapsed)
            if 'Frobenius' not in eta:
                eta['Frobenius'] = []
            eta['Frobenius'].append(elapsed)

            # by Frobenius L2 norm with filter-wised normalization
            bests, idx_bests, elapsed = by_Frobenius(
                proposals=clients, count=args.nPick, base_client=clients[c], FN=True,
                return_acc=True, test_client=tmp_client, epoch=r, show=False, log=False,
                timing=True, optimal_stopping=False)
            print("F(N)\t:", idx_bests, elapsed)
            if 'Frobenius_FN' not in eta:
                eta['Frobenius_FN'] = []
            eta['Frobenius_FN'].append(elapsed)

            # by Frobenius L2 norm with filter-wised normalization and optimal stopping
            bests, idx_bests, elapsed = by_Frobenius(
                proposals=clients, count=args.nPick, base_client=clients[c], FN=True,
                return_acc=True, test_client=tmp_client, epoch=r, show=False, log=False,
                timing=True, optimal_stopping=True)
            print("F(N&OS)\t:", idx_bests, elapsed)
            if 'Frobenius_FN_os' not in eta:
                eta['Frobenius_FN_os'] = []
            eta['Frobenius_FN_os'].append(elapsed)

    # Avg
    for key, value in eta.items():
        eta[key] = sum(value) / len(value)

    from pprint import pprint
    pprint(eta)
