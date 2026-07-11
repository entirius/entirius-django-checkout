# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import codecs
import csv
from typing import AnyStr


def read_from_csv(absolute_path: AnyStr) -> (list, AnyStr):
    if absolute_path is not None:
        path = absolute_path
    else:
        return [], "Need to define csv path"
    with open(path) as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        data = {}
        for h in headers:
            data[h] = []
        for row in reader:
            for h, v in zip(headers, row):
                if v != "":
                    data[h].append(str(v))
    return data, "Loaded"


def better_read_from_csv(absolute_path: AnyStr = None, file=None) -> (list, AnyStr):
    if absolute_path:
        with open(absolute_path) as f:
            return data_reader(f)
    elif file:
        return data_reader(codecs.iterdecode(file, "utf-8"))
    return [], "FAIL"


def data_reader(file):
    result = []
    reader = csv.DictReader(file)
    for row in reader:
        for key, value in row.items():
            if value == "":
                row[key] = None
        result.append(dict(row))

    return result, "Loaded"
