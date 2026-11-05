"""Bounded tabular readers; values are never evaluated as code."""
import csv
import io
from pathlib import Path
from zipfile import ZipFile

def read_table(upload):
    raw=upload.read()
    extension=Path(upload.name).suffix.lower()
    if extension=='.csv':
        try:
            return raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('Сохраните CSV в кодировке UTF-8.')
    output=io.StringIO();writer=csv.writer(output,delimiter=';')
    if extension=='.xlsx':
        from openpyxl import load_workbook
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                if sum(item.file_size for item in archive.infolist())>50*1024*1024:
                    raise ValueError('Распакованный Excel превышает 50 МБ.')
            workbook=load_workbook(io.BytesIO(raw),read_only=True,data_only=False,keep_links=False)
            try:
                sheet=workbook.worksheets[0]
                width=None
                for number,row in enumerate(sheet.iter_rows(max_col=21),1):
                    if number>10001:
                        raise ValueError('Не более 10 000 строк данных на первом листе.')
                    if any(cell.data_type=='f' for cell in row):
                        raise ValueError('Замените формулы на значения перед импортом Excel.')
                    values=[str(cell.value) if cell.value is not None else '' for cell in row]
                    while values and not values[-1]:
                        values.pop()
                    if len(values)>20:
                        raise ValueError('Не более 20 столбцов.')
                    if any(values):
                        if width is None:
                            width=len(values)
                        values.extend(['']*max(0,width-len(values)))
                        writer.writerow(values)
            finally:
                workbook.close()
        except ValueError:
            raise
        except Exception:
            raise ValueError('Не удалось прочитать XLSX. Проверьте файл и снимите пароль.')
    elif extension=='.xls':
        import xlrd
        try:
            workbook=xlrd.open_workbook(file_contents=raw,on_demand=True)
            try:
                sheet=workbook.sheet_by_index(0)
                if sheet.nrows>10001 or sheet.ncols>20:
                    raise ValueError('Не более 10 000 строк данных и 20 столбцов на первом листе.')
                for number in range(sheet.nrows):
                    values=[str(v) if v is not None else '' for v in sheet.row_values(number)]
                    if any(values):
                        writer.writerow(values)
            finally:
                workbook.release_resources()
        except ValueError:
            raise
        except Exception:
            raise ValueError('Не удалось прочитать XLS. Проверьте файл и снимите пароль.')
    else:
        raise ValueError('Поддерживаются CSV, XLS и XLSX.')
    return output.getvalue()
