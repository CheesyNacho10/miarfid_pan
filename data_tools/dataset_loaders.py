import json
import os
from typing import List, Tuple, Union
from spacy.tokens import Doc

import pandas as pd

from data_tools.dataset_utils import reconstruct_spacy_docs_from_json, BINARY_MAPPING_CONSPIRACY_POS, \
    BINARY_MAPPING_CRITICAL_POS, span_annot_to_spanf1_format, validate_json_annotations, is_empty_annot, \
    get_transtation_file_name

from settings import TRAIN_DATASET_EN, TRAIN_DATASET_ES, TEST_DATASET_EN, TRAIN_TRANSLATED_DATASET_FOLDER


def load_dataset_full(
        dest_lang: str, 
        src_langs: List[List[str]],
        format: str = 'docbin', 
    ) -> Union[List[Doc], str]:
    """
    Load a dataset in .json format and optionally convert it to .docbin format.

    Args:
        lang (str): Language of the dataset ('en' for English, 'es' for Spanish).
        src_langs (List[List[str]], optional): Source languages for the dataset. The format is a list of list, where each list contains the source language code and optionally additional languages for translation. Default is [], so the dataset is loaded in the specified destination language.
        format (str, optional): Format to load the dataset in ('docbin' or 'json'). Default is 'docbin'.

    Returns:
        Union[List[Doc], str]: Loaded dataset in the specified format.
    """
    dest_langs = []
    file_name = []

    for src_lang in src_langs:
        if len(src_lang) == 0:
            if dest_lang == 'en':
                dest_langs.append('en')
                file_name.append(TRAIN_DATASET_EN)
            elif dest_lang == 'es':
                dest_langs.append('es')
                file_name.append(TRAIN_DATASET_ES)
            else:
                raise ValueError(f'Unknown language: {dest_lang}')
        else:
            dest_langs.append(dest_lang)
            print(get_transtation_file_name(src_lang[0], dest_lang, src_lang[1:]))
            file_name.append(os.path.join(TRAIN_TRANSLATED_DATASET_FOLDER, get_transtation_file_name(src_lang[0], dest_lang, src_lang[1:])))
    if format == 'docbin': # HACK - This should be tested
        # dataset = reconstruct_spacy_docs_from_json(fname, lang)
        dataset = []
        for f, l in zip(file_name, dest_langs):
            if not os.path.exists(f): raise FileNotFoundError(f'File not found: {f}')
            dataset.extend(reconstruct_spacy_docs_from_json(f, l))
    elif format == 'json':
        # with open(fname, 'r', encoding='utf-8') as file:
        #     dataset = json.load(file)
        dataset = []
        for f in file_name:
            print(f'Loading {f}')
            if not os.path.exists(f): raise FileNotFoundError(f'File not found: {f}')
            with open(f, 'r', encoding='utf-8') as file:
                dataset.extend(json.load(file))
    else: raise ValueError(f'Unknown format: {format}')
    return dataset

def load_dataset_classification(
        lang: str, 
        src_langs: List[List[str]],
        string_labels: bool = False, 
        positive_class: str = 'conspiracy',
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Load the official .json dataset and convert it to a format suitable for classification.

    Args:
        lang (str): Language of the dataset ('en' or 'es').
        src_langs (List[List[str]], optional): Source languages for the dataset. The format is a list of list, where each list contains the source language code and optionally additional languages for translation. Default is [] so the dataset is loaded in the specified destination language.
        string_labels (bool, optional): If True, return original string labels from json, otherwise return binary labels. Default is False.
        positive_class (str, optional): Positive class label used in training ('conspiracy' or 'critical'). Default is 'conspiracy'.

    Returns:
        Tuple[pd.Series, pd.Series, pd.Series]: Texts, binary classes (1 - positive, 0 - negative), and text ids as pandas Series.
    """

    dataset = load_dataset_full(lang, format='json', src_langs=src_langs)
    # convert to a format suitable for classification
    texts = pd.Series([doc['text'] for doc in dataset])
    if string_labels: classes = pd.Series([doc['category'] for doc in dataset])
    else:
        if positive_class == 'conspiracy': binmap = BINARY_MAPPING_CONSPIRACY_POS
        elif positive_class == 'critical': binmap = BINARY_MAPPING_CRITICAL_POS
        else: raise ValueError(f'Unknown positive class: {positive_class}')
        classes = [binmap[doc['category']] for doc in dataset]
        classes = pd.Series(classes)
    ids = pd.Series([doc['id'] for doc in dataset])
    return texts, classes, ids

def calculate_json_dataset_stats(dset: List[dict], label: str = '') -> None:
    """
    Calculate and print the statistics for the dataset including number of documents, proportions of text 'category' classes, and span annotation classes.

    Args:
        dset (List[dict]): Dataset in the format produced by docbin_to_json() and loaded by load_official_dataset().
        label (str, optional): Label for the dataset. Default is ''.

    Returns:
        None
    """

    if label: print(f'STATISTICS FOR {label}')
    num_docs = len(dset)
    text_categ = [doc['category'] for doc in dset]
    span_annot = [doc['annotations'] for doc in dset]
    span_annot = [set([ann['category'] for ann in spans]) for spans in span_annot]
    all_span_categories = set([ann for ann_set in span_annot for ann in ann_set])
    # text category proportions
    text_categ_counts = pd.Series(text_categ).value_counts()
    text_categ_props = text_categ_counts / num_docs
    print(' ; '.join([f'{categ}: {prop*100:.3f}%' for categ, prop in text_categ_props.items() if categ in ['CRITICAL', 'CONSPIRACY']]))
    # span annotation proportions, for categories in all_span_categories
    span_annot_flags = {ann: [ann in ann_set for ann_set in span_annot] for ann in all_span_categories}
    span_annot_props = {ann: sum(flags)/num_docs for ann, flags in span_annot_flags.items()}
    span_categs = sorted(span_annot_props.items())
    print(' ; '.join([f'{ann}: {prop*100:.3f}%' for ann, prop in span_categs]))
    print()

def load_texts_and_ids_from_json(json_file: str) -> Tuple[List[str], List[str]]:
    """
    Load texts and ids from a .json file.

    Args:
        json_file (str): Path to the .json file.

    Returns:
        Tuple[List[str], List[str]]: Lists of texts and ids.
    """

    with open(json_file, 'r', encoding='utf-8') as file:
        data = json.load(file)
    texts = [item['text'] for item in data]
    ids = [item['id'] for item in data]
    return texts, ids

def load_span_annotations_from_json(json_file: str, span_f1_format: bool = True) -> Union[dict, List[List[dict]]]:
    """
    Load span annotations from a .json file.

    Args:
        json_file (str): Path to the .json file.
        span_f1_format (bool, optional): If True, return annotations in the format used for span-F1 score calculation. Default is True.

    Returns:
        Union[dict, List[List[dict]]]: Annotations formatted for span-F1 calculation or as in the original .json file.
    """

    with open(json_file, 'r', encoding='utf-8') as file:
        data = json.load(file)
    if span_f1_format:
        result = {}
        for item in data:
            f1annot = []
            annots = item['annotations']
            validate_json_annotations(annots)
            if not is_empty_annot(annots):
                for annot in annots: f1annot.append(span_annot_to_spanf1_format(annot))
            result[item['id']] = f1annot
        return result
    else:
        return [item['annotations'] for item in data]

if __name__ == '__main__':
    #calculate_json_dataset_stats(load_dataset_full('en', format='json'), label='EN')
    load_span_annotations_from_json(TEST_DATASET_EN, span_f1_format=True)
