import os
import urllib
import zipfile
from argparse import ArgumentParser
from collections import defaultdict

import numpy as np
import pandas as pd
from tqdm import tqdm

from load import implicit_load

from mlperf_compliance import mlperf_log


MIN_RATINGS = 20


USER_COLUMN = 'user_id'
ITEM_COLUMN = 'item_id'


TRAIN_RATINGS_FILENAME = 'train-ratings.csv'
TEST_RATINGS_FILENAME = 'test-ratings.csv'
TEST_NEG_FILENAME = 'test-negative.csv'


def parse_args():
    parser = ArgumentParser()
    parser.add_argument('--dataset', nargs='?', default='ml-20m', choices=['ml-1m', 'ml-20m'],
                        help='The dataset name, temporary support ml-1m and ml-20m.')
    parser.add_argument('path', type=str, default = './data/',
                        help='Path to reviews CSV file from MovieLens')
    parser.add_argument('-n', '--negatives', type=int, default=999,
                        help='Number of negative samples for each positive'
                             'test example')
    parser.add_argument('-s', '--seed', type=int, default=0,
                        help='Random seed to reproduce same negative samples')
    return parser.parse_args()

def get_movielens_data(data_dir, dataset):
    if not os.path.exists(data_dir + '%s.zip' % dataset):
        os.mkdir(data_dir)
        urllib.request.urlretrieve('http://files.grouplens.org/datasets/movielens/%s.zip' % dataset, data_dir + dataset + '.zip')
        with zipfile.ZipFile(data_dir + "%s.zip" % dataset, "r") as f:
            f.extractall(data_dir + "./")

def main():
    args = parse_args()
    np.random.seed(args.seed)

    print("download movielens {} dataset".format(args.dataset))
    get_movielens_data(args.path, args.dataset)
    output = os.path.join(args.path, args.dataset)

    print("Loading raw data from {}".format(output))
    df = implicit_load(os.path.join(output,"ratings.csv"), sort=False)

    print("Filtering out users with less than {} ratings".format(MIN_RATINGS))
    grouped = df.groupby(USER_COLUMN)
    mlperf_log.ncf_print(key=mlperf_log.PREPROC_HP_MIN_RATINGS, value=MIN_RATINGS)
    df = grouped.filter(lambda x: len(x) >= MIN_RATINGS)

    print("Mapping original user and item IDs to new sequential IDs")
    original_users = df[USER_COLUMN].unique()
    original_items = df[ITEM_COLUMN].unique()

    user_map = {user: index for index, user in enumerate(original_users)}
    item_map = {item: index for index, item in enumerate(original_items)}

    df[USER_COLUMN] = df[USER_COLUMN].apply(lambda user: user_map[user])
    df[ITEM_COLUMN] = df[ITEM_COLUMN].apply(lambda item: item_map[item])

    assert df[USER_COLUMN].max() == len(original_users) - 1
    assert df[ITEM_COLUMN].max() == len(original_items) - 1

    print("Creating list of items for each user")
    # Need to sort before popping to get last item
    df.sort_values(by='timestamp', inplace=True)
    all_ratings = set(zip(df[USER_COLUMN], df[ITEM_COLUMN]))
    user_to_items = defaultdict(list)
    for row in tqdm(df.itertuples(), desc='Ratings', total=len(df)):
        user_to_items[getattr(row, USER_COLUMN)].append(getattr(row, ITEM_COLUMN))  # noqa: E501

    test_ratings = []
    test_negs = []
    all_items = set(range(len(original_items)))

    print("Generating {} negative samples for each user"
          .format(args.negatives))
    mlperf_log.ncf_print(key=mlperf_log.PREPROC_HP_NUM_EVAL, value=args.negatives)

    # The default of np.random.choice is replace=True
    mlperf_log.ncf_print(key=mlperf_log.PREPROC_HP_SAMPLE_EVAL_REPLACEMENT, value=True)

    #===========================================================================
    #== First random operation triggers the clock start. =======================
    #===========================================================================
    mlperf_log.ncf_print(key=mlperf_log.RUN_START)
    mlperf_log.ncf_print(key=mlperf_log.INPUT_STEP_EVAL_NEG_GEN)

    for user in tqdm(range(len(original_users)), desc='Users', total=len(original_users)):  # noqa: E501
        test_item = user_to_items[user].pop()

        all_ratings.remove((user, test_item))
        all_negs = all_items - set(user_to_items[user])
        all_negs = sorted(list(all_negs))  # determinism

        test_ratings.append((user, test_item))
        test_negs.append(list(np.random.choice(all_negs, args.negatives)))

    print("Saving train and test CSV files to {}".format(output))
    df_train_ratings = pd.DataFrame(list(all_ratings))
    df_train_ratings['fake_rating'] = 1
    df_train_ratings.to_csv(os.path.join(output, TRAIN_RATINGS_FILENAME),
                            index=False, header=False, sep='\t')

    mlperf_log.ncf_print(key=mlperf_log.INPUT_SIZE, value=len(df_train_ratings))

    df_test_ratings = pd.DataFrame(test_ratings)
    df_test_ratings['fake_rating'] = 1
    df_test_ratings.to_csv(os.path.join(output, TEST_RATINGS_FILENAME),
                           index=False, header=False, sep='\t')

    df_test_negs = pd.DataFrame(test_negs)
    df_test_negs.to_csv(os.path.join(output, TEST_NEG_FILENAME),
                        index=False, header=False, sep='\t')


if __name__ == '__main__':
    main()