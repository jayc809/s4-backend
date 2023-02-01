from io import BytesIO
from flask import Response
import os
from datetime import datetime


def pil_img_to_io(img):
    img_io = BytesIO()
    img.save(img_io, 'PNG', quality=70)
    img_io.seek(0)
    return img_io


def send_404(message):
    data = "{'errorMessage':'" + message + "'}"
    return Response(data, status=404, mimetype='application/json')


def send_200(message, data=None):
    if not data:
        data = "{'successMessage':'" + message + "'}"
    return Response(data, status=200, mimetype='application/json')


def remove_files(files):
    for filename in files:
        try:
            os.remove(filename)
        except:
            continue


def reset_lp(lp, window_id):
    lp.window_id = window_id
    lp.twoFA_verified = False
    # lp.fc_verified = False
    lp.date_created = datetime.utcnow()


def directory_to_dict(directory):
    return {
        "id": directory.id,
        "parentId": directory.parent_id,
        "name": directory.name,
        "username": directory.username,
        "dateCreated": directory.date_created
    }


def file_to_dict(file):
    return {
        "id": file.id,
        "directoryId": file.directory_id,
        "username": file.username,
        "dateCreated": file.date_created,
        "name": file.name,
        "s3Name": file.s3_name,
        "contentType": file.content_type
    }