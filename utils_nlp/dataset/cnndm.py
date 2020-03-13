# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

# This script reuses some code from https://github.com/nlpyang/BertSum

"""
    Utility functions for downloading, extracting, and reading the
    CNN/DM dataset at https://github.com/harvardnlp/sent-summary.

"""

import nltk

# nltk.download("punkt")
from nltk import tokenize
from nltk.tokenize.treebank import TreebankWordDetokenizer
import os
import sys
import regex as re
from torchtext.utils import download_from_url, extract_archive
import zipfile


from utils_nlp.dataset.url_utils import (
    maybe_download,
    maybe_download_googledrive,
    extract_zip,
)
from utils_nlp.models.transformers.datasets import (
    SummarizationDataset,
    IterableSummarizationDataset,
)


## have to move these local functions outside due to use of pool of processes
REMAP = {
    "-lrb-": "(",
    "-rrb-": ")",
    "-lcb-": "{",
    "-rcb-": "}",
    "-lsb-": "[",
    "-rsb-": "]",
    "``": '"',
    "''": '"',
}


def _clean(x):
    return re.sub(
        r"-lrb-|-rrb-|-lcb-|-rcb-|-lsb-|-rsb-|``|''",
        lambda m: REMAP.get(m.group()),
        x,
    )

def _remove_ttags(line):
    line = re.sub(r"<t>", "", line)
    # change </t> to <q>
    # pyrouge test requires <q> as sentence splitter
    line = re.sub(r"</t>", "<q>", line)
    return line

def _target_sentence_tokenization(line):
    return line.split("<q>")

def join(sentences):
    return " ".join(sentences)


def CNNDMSummarizationDataset(*args, **kwargs):
    """Load the CNN/Daily Mail dataset preprocessed by harvardnlp group."""



    URLS = ["https://s3.amazonaws.com/opennmt-models/Summary/cnndm.tar.gz"]

    def _setup_datasets(url, top_n=-1, local_cache_path=".data", prepare_extractive=True):
        FILE_NAME = "cnndm.tar.gz"
        maybe_download(url, FILE_NAME, local_cache_path)
        dataset_tar = os.path.join(local_cache_path, FILE_NAME)
        extracted_files = extract_archive(dataset_tar)
        for fname in extracted_files:
            if fname.endswith("train.txt.src"):
                train_source_file = fname
            if fname.endswith("train.txt.tgt.tagged"):
                train_target_file = fname
            if fname.endswith("test.txt.src"):
                test_source_file = fname
            if fname.endswith("test.txt.tgt.tagged"):
                test_target_file = fname

        if prepare_extractive:

            return (
                IterableSummarizationDataset(
                    train_source_file,
                    train_target_file,
                    [_clean, tokenize.sent_tokenize],
                    [_clean, _remove_ttags, _target_sentence_tokenization],
                    nltk.word_tokenize,
                    top_n,
                ),
                IterableSummarizationDataset(
                    test_source_file,
                    test_target_file,
                    [_clean, tokenize.sent_tokenize],
                    [_clean, _remove_ttags, _target_sentence_tokenization],
                    nltk.word_tokenize,
                    top_n,
                ),
            )
        else:
            return (
                SummarizationDataset(
                    train_source_file,
                    train_target_file,
                    [_clean, tokenize.sent_tokenize],
                    [_clean, _remove_ttags, _target_sentence_tokenization],
                    top_n,
                    16
                ),
                SummarizationDataset(
                    test_source_file,
                    test_target_file,
                    [_clean, tokenize.sent_tokenize],
                    [_clean, _remove_ttags, _target_sentence_tokenization],
                    top_n,
                    16
                ),
            )
        

    return _setup_datasets(*((URLS[0],) + args), **kwargs)


class CNNDMBertSumProcessedData:
    """Class to load dataset preprocessed by BertSum paper at
        https://github.com/nlpyang/BertSum
    """

    @staticmethod
    def download(local_path=".data"):
        file_name = "bertsum_data.zip"
        url = "https://drive.google.com/uc?export=download&"
        "id=1x0d61LP9UAN389YN00z0Pv-7jQgirVg6"
        try:
            if os.path.exists(os.path.join(local_path, file_name)):
                downloaded_zipfile = zipfile.ZipFile(
                    os.path.join(local_path, file_name)
                )
            else:
                dataset_zip = download_from_url(url, root=local_path)
                downloaded_zipfile = zipfile.ZipFile(dataset_zip)
        except:
            print("Unexpected dataset downloading or reading error:", sys.exc_info()[0])
            raise

        downloaded_zipfile.extractall(local_path)
        return local_path


def _detokenize(line):
    """
    Detokenizes the processed CNN/DM dataset to recover the original dataset,
    e.g. converts "-LRB-" back to "(" and "-RRB-" back to ")".
    """
    line = line.strip().replace("``", '"').replace("''", '"').replace("`", "'")
    twd = TreebankWordDetokenizer()
    s_list = [
        twd.detokenize(x.strip().split(" "), convert_parentheses=True)
        for x in line.split("<S_SEP>")
    ]
    return " ".join(s_list)


def CNNDMSummarizationDatasetOrg(
    local_path=".", top_n=-1, return_iterable=False, return_dev_data=False
):
    """
    Downloads a version of the CNN/DailyMail dataset with minimal processing
    from https://github.com/microsoft/unilm/tree/master/unilm-v1
    This version of the CNN/DM dataset was originally downloaded from
    https://github.com/harvardnlp/sent-summary
    and preprocessed following https://github.com/abisee/cnn-dailymail.

    Args:
        local_path (str): Path to store the downloaded data. If the data file
            doesn't exist in this path, it's downloaded and unzipped.
        top_n (int): Number of lines to read. Defaults to -1 and the entire dataset
            is read.
        return_iterable (bool): If False, returns SummarizationDataset.
            If True, returns IterableSummarizationDataset. Defaults to False.
        return_dev_data (bool): if False, returns train and test data splits.
            If True, returns train, test, and dev data splits. Defaults to False.

    Returns:
        tuple: tuple containing train, test (, and dev) datasets.
    """

    # Download and unzip the data
    FILE_ID = "1jiDbDbAsqy_5BM79SmX6aSu5DQVCAZq1"
    FILE_NAME = "cnndm_data.zip"

    output_dir = os.path.join(local_path, "cnndm_data")
    os.makedirs(output_dir, exist_ok=True)

    # This folder contains the a version of the dataset with minimal processing
    org_data_dir = os.path.join(output_dir, "org_data")

    expected_data_files = set(
        [
            "train.src",
            "org_data",
            "dev.src",
            "test.tgt",
            "train.tgt",
            "dev.tgt",
            "test.src",
        ]
    )
    expected_org_data_files = set(
        [
            "training.summary",
            "test.article",
            "dev.article",
            "training.article",
            "dev.summary",
            "test.summary",
        ]
    )

    maybe_download_googledrive(
        google_file_id=FILE_ID, file_name=FILE_NAME, work_directory=local_path
    )

    if (
        set(os.listdir(output_dir)) != expected_data_files
        or set(os.listdir(org_data_dir)) != expected_org_data_files
    ):
        extract_zip(
            file_path=os.path.join(local_path, FILE_NAME),
            dest_path=os.path.join(local_path, output_dir),
        )

    train_source_file = os.path.join(org_data_dir, "training.article")
    train_target_file = os.path.join(org_data_dir, "training.summary")
    test_source_file = os.path.join(org_data_dir, "test.article")
    test_target_file = os.path.join(org_data_dir, "test.summary")
    dev_source_file = os.path.join(org_data_dir, "dev.article")
    dev_target_file = os.path.join(org_data_dir, "dev.summary")

    source_preprocessing = [_detokenize]
    target_preprocessing = [_detokenize]

    if return_iterable:
        train_dataset = IterableSummarizationDataset(
            source_file=train_source_file,
            target_file=train_target_file,
            source_preprocessing=source_preprocessing,
            target_preprocessing=target_preprocessing,
            top_n=top_n,
        )

        test_dataset = IterableSummarizationDataset(
            source_file=test_source_file,
            target_file=test_target_file,
            source_preprocessing=source_preprocessing,
            target_preprocessing=target_preprocessing,
            top_n=top_n,
        )
    else:
        train_dataset = SummarizationDataset(
            source_file=train_source_file,
            target_file=train_target_file,
            source_preprocessing=source_preprocessing,
            target_preprocessing=target_preprocessing,
            top_n=top_n,
        )

        test_dataset = SummarizationDataset(
            source_file=test_source_file,
            target_file=test_target_file,
            source_preprocessing=source_preprocessing,
            target_preprocessing=target_preprocessing,
            top_n=top_n,
        )

    if return_dev_data:
        if return_iterable:
            dev_dataset = IterableSummarizationDataset(
                source_file=dev_source_file,
                target_file=dev_target_file,
                source_preprocessing=source_preprocessing,
                target_preprocessing=target_preprocessing,
                top_n=top_n,
            )
        else:
            dev_dataset = SummarizationDataset(
                source_file=dev_source_file,
                target_file=dev_target_file,
                source_preprocessing=source_preprocessing,
                target_preprocessing=target_preprocessing,
                top_n=top_n,
            )

        return train_dataset, test_dataset, dev_dataset
    else:
        return train_dataset, test_dataset
