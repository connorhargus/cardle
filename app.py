import ast
from collections import Counter
import csv
from pathlib import Path
import shlex
import sqlite3

from deep_translator import GoogleTranslator
import pandas as pd
from shiny import App, reactive, render, ui


app_ui = ui.page_fluid(
    ui.panel_title('Kindle Vocab to Flashcard Generator'),
    ui.layout_sidebar(
        ui.panel_sidebar(
            ui.p("""Upload your Kindle's vocab.db file and convert your searched words 
                    to flashcards for spaced repetition practice! This app will output text in a format 
                    suitable for import to Knowt, Quizlet, Brainscape, etc."""
            ),
            ui.p("""You can get your Kindle's 
                    vocab.db by connecting it to your computer and searching in the file system. Translations
                    use Google Translate, and will take some time if you have a large vocabulary."""
            ),
            ui.p("""If this helps you, please consider buying me a coffee so I will be
                    motivated to keep the site up and running :)   - Connor"""
            ),
            ui.a(
                ui.img(src='github.png', width=45, height=45),
                href='https://github.com/connorhargus/cardle',
            ),
            ui.a(
                ui.img(src='bmc-button.png', width=150, height=45),
                href='https://www.buymeacoffee.com/connorhargus',
            ),
        ),
        ui.panel_main(
            ui.div({"class": "card mb-3"},
                ui.div({"class": "card-body"},
                    ui.row(
                        ui.column(6,
                            ui.input_checkbox("word_translate", "Word translation", True),
                            ui.input_checkbox("usage_translate", "Phrase translation", True),
                            ui.input_checkbox("bold_word", "Bold original word", False),
                            ui.input_checkbox("bold_usage", "Bold original usage", False),
                            ui.input_checkbox("italic_translated_word", "Italic translated word", False),
                            ui.input_checkbox("italic_translated_usage", "Italic translated sentence", False),
                        ),
                        ui.column(6,
                            ui.input_selectize("col_delimiter", "Column delimiter", ['\\t', '\\n', ';']),
                            ui.input_text("row_delimiter", "Line delimiter", r'\n\n'),
                            ui.input_numeric("max_usage_length", "Max sentence characters", 250),
                        ),
                    ),
                    ui.input_file("file1", "Choose a file to upload:", multiple=True),
                    ui.input_action_button("submit", "Convert"),
                ),
            ),
        ),
    ),
    ui.output_text_verbatim("output_text"),
)


def server(input, output, session):
    MAX_SIZE = 50000000  # 50 MB max size for uploaded database
    # Language you wish to translate to (probably your native language), default: English
    to_lang = 'en'

    output_str = reactive.Value('')

    @output
    @render.text
    def output_text():
        return output_str()

    @reactive.Effect
    @reactive.event(input.submit)
    def convert():
        file_info = input.file1()
        if not file_info:
            return

        if file_info[0]["size"] > MAX_SIZE:
            m = ui.modal(
                "This file is too large. You must be learning a lot of vocab!",
                title="Somewhat important message",
                easy_close=True,
                footer=None,
            )
            ui.modal_show(m)
            return

        datapath = file_info[0]["datapath"]

        con = sqlite3.connect(datapath)
        vocab = pd.read_sql_query('SELECT word_key, usage FROM LOOKUPS', con)
        con.close()

        # Create columns for original language, word looked up, number of times word was looked up, and sentence length
        vocab['from_lang'] = vocab['word_key'].str[:2]
        vocab['word'] = vocab['word_key'].str[3:]
        vocab = vocab.drop(['word_key'], axis=1)
        word_counter = Counter(list(vocab['word']))
        vocab['lookups'] = vocab['word'].map(word_counter)
        vocab['length'] = vocab['usage'].str.len()

        # Keep only sentences reasonably short
        vocab = vocab[vocab['length'] < input.max_usage_length()]

        # Sort by language and then prioritize by number of times looked up
        vocab.sort_values(['from_lang', 'lookups', 'length'], ascending=[True, False, True], inplace=True)
        vocab = vocab.drop_duplicates(subset=['word'], keep='first')

        # vocab = vocab[vocab['lookups'] > 1]  # Keep only words which have been looked up more than once
        # vocab = vocab[:5]  # Shrink the dataframe when debugging this code to avoid unnecessary translate API calls.

        vocab = vocab.reset_index(drop=True)

        if input.word_translate() or input.usage_translate():
            with ui.Progress(min=1, max=len(vocab)) as p:
                p.set(message="Translation in progress", detail="Contacting Google Translate...")
                # Function to apply to word and usage columns in dataframe to translate to to_lang via Google Translate
                def translate_(row, key):
                    translated = row.name
                    to_trans = '\"' + row[key][0:20] + ('...' if len(row[key]) > 20 else '') + '\"'
                    message = f"Translation of {key} in progress"
                    p.set(translated, message=to_trans, detail=message)
                    translator = GoogleTranslator(source=row['from_lang'], target=to_lang)
                    return translator.translate(str(row[key]))

                # Perform translations (English: en, Chinese: zh, Portuguese: pt)
                if input.word_translate():
                    vocab['word_translated'] = vocab.apply(translate_, key='word', axis=1)
                if input.usage_translate():
                    vocab['usage_translated'] = vocab.apply(translate_, key='usage', axis=1)

        vocab['word'] = vocab['word'].str.lower()
        if input.bold_word():
            vocab['word'] = '**' + vocab['word'].str.lower() + '**'

        vocab['usage'] = vocab.apply(lambda x: x['usage'].replace(x['word'], '_____'), axis=1)
        vocab['usage'] = vocab['usage'].str.rstrip()
        if input.bold_usage():
            vocab['usage'] = '**' + vocab['usage'] + '**'

        if input.word_translate():
            vocab['word_translated'] = vocab['word_translated'].str.lower()
            if input.italic_translated_word():
                vocab['word_translated'] = '*' + vocab['word_translated'] + '*'

        if input.usage_translate():
            if input.italic_translated_usage():
                vocab['usage_translated'] = '*' + vocab['usage_translated'] + '*'

        vocab['definition'] = vocab['usage'] + ('\n' + vocab['word_translated'] if input.word_translate() else '') + ('\n' + vocab['usage_translated'] if input.usage_translate() else '')
        vocab = vocab[['word', 'definition']]

        # Dealing with \\ characters interpreted by input file: see https://stackoverflow.com/questions/54410812/how-do-you-input-escape-sequences-in-python
        escape_col_delimiter = ast.literal_eval(shlex.quote(input.col_delimiter()))
        escape_row_delimiter = ast.literal_eval(shlex.quote(input.row_delimiter()))

        # Output to a csv format suitable for copy and paste into flashcard website
        vocab_string = vocab.to_csv(None, sep=escape_col_delimiter, header=False, index=False, lineterminator=escape_row_delimiter,
                     quoting=csv.QUOTE_NONE, quotechar="", escapechar=" ")

        output_str.set(vocab_string)


static_dir = Path(__file__).parent / "static"
app = App(app_ui, server, static_assets=static_dir)
