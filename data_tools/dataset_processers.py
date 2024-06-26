"""
All functions that are used to process the dataset.

They all should accept this arguments or a subset of them:
    Tuple[pd.Series, pd.Series, pd.Series]: Texts, binary classes (1 - positive, 0 - negative), and text ids as pandas Series.

And return the same type of data.
"""

import json
import os
from typing import List, Tuple, Optional
from itertools import permutations
import pandas as pd
from tqdm import tqdm
from transformers import MarianMTModel, MarianTokenizer, BatchEncoding
from pathlib import Path
import numpy as np

from data_tools.dataset_loaders import load_dataset_full
from data_tools.dataset_class import DatasetElement, dataset_to_dict, dataset_from_dict
from data_tools.dataset_utils import get_transtation_file_name
from settings import TRAIN_DATASET_EN, TRAIN_DATASET_ES, TRAIN_TRANSLATED_DATASET_FOLDER

def _load_translation_model(
        src_lang: str,
        dest_lang: str,
        device: str,
    ) -> Tuple[MarianMTModel, MarianTokenizer]:
    """
    Load a translation model for the specified source and destination languages.

    Args:
        src_lang (str): Source language code.
        dest_lang (str): Destination language code.
        device (str): Device to use for the model.

    Returns:
        Tuple[MarianMTModel, MarianTokenizer]: Translation model and tokenizer.
    """

    model_name = f'Helsinki-NLP/opus-mt-{src_lang}-{dest_lang}'
    tokenizer = MarianTokenizer.from_pretrained(model_name)
    model = MarianMTModel.from_pretrained(model_name).to(device)
    return model, tokenizer

def _get_tokenized_chunks(text: str, tokenizer: MarianTokenizer, max_length: int) -> List[BatchEncoding]:
    """
    Split the input text into tokenized chunks of the specified maximum length.
    
    Args:
        text (str): Text to split.
        tokenizer (MarianTokenizer): Tokenizer for the translation model.
        max_length (int): Maximum length of the chunks.

    Returns:
        List[BatchEncoding]: List of tokenized chunks.
    """
    tokens = tokenizer(text, return_tensors='pt', truncation=False, verbose=False)

    tokenized_chunks = []

    # Because the tokenizer returns [1, n] tensors, we need to squeeze the first dimension
    input_ids = tokens['input_ids'].squeeze(0)
    attention_mask = tokens['attention_mask'].squeeze(0)

    for i in range(0, len(input_ids), max_length):
        chunk_input_ids = input_ids[i:i + max_length]
        chunk_attention_mask = attention_mask[i:i + max_length]

        # Same format as input: we need to unsqueeze the first dimension
        chunk = BatchEncoding({
            'input_ids': chunk_input_ids.unsqueeze(0),
            'attention_mask': chunk_attention_mask.unsqueeze(0)
        })
        tokenized_chunks.append(chunk)
    return tokenized_chunks

def _translate_text(text: str, tokenizer: MarianTokenizer, model: MarianMTModel) -> str:
    """
    Translate the input text using the specified translation model.

    Args:
        text (str): Text to translate.
        tokenizer (MarianTokenizer): Tokenizer for the translation model.
        model (MarianMTModel): Translation model.

    Returns
        str: Translated text.
    """
    max_length = model.config.max_length
    tokenized_chunks = _get_tokenized_chunks(text, tokenizer, max_length // 2)

    transladed_chunks = []
    for chunk in tokenized_chunks:
        chunk.to(model.device)
        chunk_outputs = model.generate(**chunk)
        translated_chunk = tokenizer.decode(chunk_outputs[0], skip_special_tokens=True)
        transladed_chunks.append(translated_chunk)
    return ' '.join(transladed_chunks)

def _translate_dataset(
    src_lang: str,
    dest_lang: str,
    transition_langs: List[str] = [],
    device: str = 'cuda',
) -> List[DatasetElement]:
    """
    Translate the dataset to the specified destination language.

    Args:
        dataset (List[DatasetElement]): Dataset to translate.
        src_lang (str): Source language code.
        dest_lang (str): Destination language code.
        transition_langs (List[str], optional): Intermediate languages to use for the translation. Default is [].
        device (str, optional): Device to use for the model. Default is 'cuda'.
    
    Returns:
        List[DatasetElement]: Translated dataset.
    """
    translated_dataset = dataset_from_dict(load_dataset_full(src_lang, format='json', src_langs=[[src_lang]]))
    if len(transition_langs) == 0 and src_lang == dest_lang:
        return translated_dataset

    translation_langs = [src_lang] + transition_langs + [dest_lang]
    for src_lang, dest_lang in zip(translation_langs[:-1], translation_langs[1:]):
        if src_lang == dest_lang:
            raise ValueError('Source and destination languages must be different for each transition.')

    new_src_index = 0
    for last_transition_index in range(len(translation_langs) - 1, 0, -1):
        src_lang = translation_langs[0]
        dest_lang = translation_langs[last_transition_index]
        mid_langs = translation_langs[1:last_transition_index]
        if os.path.exists(os.path.join(TRAIN_TRANSLATED_DATASET_FOLDER, get_transtation_file_name(src_lang, dest_lang, mid_langs))):
            print(f"Cached translation found for '{dest_lang}' using {[src_lang] + mid_langs}")
            new_src_index = last_transition_index
            translated_dataset = dataset_from_dict(load_dataset_full(dest_lang, format='json', src_langs=[[src_lang] + mid_langs]))
            break

    for lang_index in range(new_src_index, len(translation_langs) - 1):
        src_lang = translation_langs[lang_index]
        dest_lang = translation_langs[lang_index+1]
        model, tokenizer = _load_translation_model(src_lang, dest_lang, device)

        for element in tqdm(translated_dataset, desc=f"Translating texts from '{src_lang}' to '{dest_lang}' using {translation_langs[:lang_index+1]}"):
            element.id = f'{element.id.split("_")[0]}_{"_".join(translation_langs[:lang_index+2])}'
            element.text = _translate_text(element.text, tokenizer, model)

        dataset_dict = dataset_to_dict(translated_dataset)
        with open(os.path.join(TRAIN_TRANSLATED_DATASET_FOLDER, get_transtation_file_name(translation_langs[0], dest_lang, translation_langs[1:lang_index+1])), 'w') as file:
            json.dump(dataset_dict, file, ensure_ascii=False, indent=4)
    return translated_dataset

def get_translated_dataset(
    src_lang: str,
    dest_lang: str,
    transition_langs: List[str] = [],
) -> List[DatasetElement]:
    """
    Load the dataset in the source language, translate it to the destination language, and return the translated dataset.

    Args:
        src_lang (str): Source language code.
        dest_lang (str): Destination language code.
        transition_langs (List[str], optional): Intermediate languages that could have been used for the translation. Default is [].

    Returns:
        List[DatasetElement]: Translated dataset.
    """
    if src_lang == dest_lang and len(transition_langs) == 0:
        return dataset_from_dict(load_dataset_full(dest_lang, format='json', src_langs=[[dest_lang]]))
    os.makedirs(TRAIN_TRANSLATED_DATASET_FOLDER, exist_ok=True)

    try:
        dataset = dataset_from_dict(load_dataset_full(dest_lang, format='json', src_langs=[[src_lang] + transition_langs]))
    except FileNotFoundError:
        dataset = _translate_dataset(src_lang, dest_lang, transition_langs)
    return dataset

def mask_texts(
        texts: pd.Series,
        mask_token: str,
        lang: str,
        default_mask_prob: float,
        special_mask_prob: float,
        mask_word_list: Optional[List[str]] = None,
        ) -> pd.Series:
    """
    Mask specific words in the dataset texts.
    
    Args:
        texts (pd.Series): Texts to be processed.
    Returns:
        pd.Series: Processed texts.
    """
    if mask_word_list is None:
        mask_word_list = load_mask_words(lang)

    def mask_words(text: str) -> str:
        words = text.split(' ')
        masked_words = []

        for word in words:
            if word.lower() in mask_word_list:
                mask_prob = special_mask_prob
            else:
                mask_prob = default_mask_prob

            if np.random.rand() < mask_prob:
                masked_words.append(mask_token)
            else:
                masked_words.append(word)
        
        masked_text = ' '.join(masked_words)
        return masked_text

    masked_texts = texts.apply(mask_words)
    return masked_texts

def load_mask_words(lang: str) -> List[str]:
    """
    Load the words to be masked from a file.

    Args:
        file_path (str): Path to the file containing the words to be masked.

    Returns:
        List[str]: List of words to be masked.
    """
    file_path = Path(__file__).parent / 'mask_words' / f'{lang}_words.txt'
    with open(file_path, 'r') as file:
        mask_words = file.read().splitlines()

    print(f"Loaded {len(mask_words)} words from '{file_path}'. ")
    return mask_words


if __name__ == '__main__':
    main_langs = ['en', 'es']
    transition_langs = ['en', 'es', 'fr', 'de', 'it']
    for src_lang in main_langs:
        for dest_lang in main_langs:
            for perm in permutations(transition_langs):
                # for lenght in range(1, len(perm)+1):
                for lenght in range(1, 2):
                    trans_langs = list(perm[:lenght])
                    if src_lang == trans_langs[0] or dest_lang == trans_langs[-1]:
                        continue
                    get_translated_dataset(src_lang, dest_lang, trans_langs)
                    print(f"Translation from '{src_lang}' to '{dest_lang}' using {trans_langs} completed.")
