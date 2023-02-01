from flask import Flask, request, redirect, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
import bcrypt
import random
import json
from io import BytesIO
from decouple import config
from base64 import b64decode
import boto3
import os
import pyotp
from collections import deque

from send_verification_email import send_verification_email
from twoFA import generate_twoFA_code, compare_twoFA_code
from utils import pil_img_to_io, send_200, send_404, remove_files, reset_lp, directory_to_dict, file_to_dict
# from fc import compare_face_data

dev_mode = False

app = Flask(__name__)
CORS(app)

app.config["SQLALCHEMY_DATABASE_URI"] = config("DATABASE_URL")
db = SQLAlchemy(app)
session = boto3.Session(
    aws_access_key_id=config("AWS_ACCESS_KEY"),
    aws_secret_access_key=config("AWS_SECRET_KEY"),
    profile_name="default"
)
s3_client = session.client("s3")
# bucket = s3.Bucket(config("AWS_BUCKET_NAME"))


class VerificationCode(db.Model):
    username = db.Column(db.String(100), primary_key=True)
    code = db.Column(db.String(6), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    verified = db.Column(db.Boolean, default=False)


class TwoFACode(db.Model):
    username = db.Column(db.String(100), primary_key=True)
    key = db.Column(db.String(100), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    verified = db.Column(db.Boolean, default=False)


class FaceRecognition(db.Model):
    username = db.Column(db.String(100), primary_key=True)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    verified = db.Column(db.Boolean, default=False)


class User(db.Model):
    username = db.Column(db.String(100), primary_key=True)
    password = db.Column(db.String(200), nullable=False)
    security_question = db.Column(db.String(20), nullable=False)
    security_answer = db.Column(db.String(100), nullable=False)
    secret = db.Column(db.String(100), default="")
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    entry_directory = db.Column(db.Integer, nullable=False)


class LoginProcess(db.Model):
    username = db.Column(db.String(100), primary_key=True)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    window_id = db.Column(db.String(100), nullable=False)
    twoFA_verified = db.Column(db.Boolean, default=False)
    fc_verified = db.Column(db.Boolean, default=False)


class Directory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(100), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)


class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    directory_id = db.Column(db.Integer, nullable=False)
    username = db.Column(db.String(100), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    name = db.Column(db.String(100), nullable=False)
    s3_name = db.Column(db.String(100))
    content_type = db.Column(db.String(45), nullable=False)


with app.app_context():
    db.create_all()
    

def validate_lp(username, window_id):
    lp = LoginProcess.query.get(username)
    if not lp:
        return False
    if (
        (datetime.utcnow() - lp.date_created).total_seconds() > 6000 or
        window_id != lp.window_id or
        not lp.twoFA_verified #or
        # not lp.fc_verified
    ):
        reset_lp(lp, window_id)
        return False
    return True

@app.route("/get_entry_directory")
def get_entry_directory():
    username = request.args.get("username", None)
    window_id = request.args.get("windowId", None)
    if not (username and window_id):
        return send_404("no credentials found")
    try:
        if not validate_lp(username, window_id) and not dev_mode:
            return send_404("invalid session")
        user = User.query.get(username)
        if not user:
            return send_404("no user found")
        if not user.entry_directory:
            return send_404("no entry directory found")
        data = '{"entryDirectoryId":"' + str(user.entry_directory) + \
            '", "successMessage":"entry found"}'
        return send_200("", data=data)
    except Exception as e:
        print(e)
        return send_404("db error")


@app.route("/get_directory")
def get_directory():
    username = request.args.get("username", None)
    directory_id = request.args.get("directoryId", None)
    window_id = request.args.get("windowId", None)
    if not (username and directory_id and window_id):
        return send_404("no credentials found")
    try:
        if not validate_lp(username, window_id) and not dev_mode:
            return send_404("invalid session")
        directory = Directory.query.get(directory_id)
        subdirectories = Directory.query.filter_by(
            parent_id=directory_id).all()
        for i, subdirectory in enumerate(subdirectories):
            subdirectories[i] = directory_to_dict(subdirectory)
        files = File.query.filter_by(directory_id=directory_id).all()
        for i, file in enumerate(files):
            files[i] = file_to_dict(file)
        return jsonify({
            "id": directory.id,
            "name": directory.name,
            "subdirectories": subdirectories,
            "files": files
        })
    except Exception as e:
        print(e)
        return send_404("db error")


@app.route("/create_directory")
def create_directory():
    username = request.args.get("username", None)
    directory_name = request.args.get("directoryName", None)
    parent_directory_id = request.args.get("parentDirectoryId", None)
    window_id = request.args.get("windowId", None)
    if not (username and directory_name and parent_directory_id and window_id):
        return send_404("no credentials found")
    try:
        if not validate_lp(username, window_id) and not dev_mode:
            return send_404("invalid session")
        duplicates = Directory.query.filter_by(parent_id = parent_directory_id, name=directory_name, username=username).all()
        if duplicates:
            return send_404("directory already exists")
        new_directory = Directory(parent_id=parent_directory_id, name=directory_name, username=username)
        db.session.add(new_directory)
        db.session.commit()
        new_directory = Directory.query.filter_by(parent_id = parent_directory_id, name=directory_name, username=username).first()
        return jsonify(directory_to_dict(new_directory))
    except Exception as e:
        print(e)
        return send_404("db failed")


@app.route("/delete_directory")
def delete_directory():
    username = request.args.get("username", None)
    directory_name = request.args.get("directoryName", None)
    directory_id = request.args.get("directoryId", None)
    window_id = request.args.get("windowId", None)
    if not (username and directory_name and directory_id and window_id):
        return send_404("no credentials found")
    try:
        if not validate_lp(username, window_id) and not dev_mode:
            return send_404("invalid session")
        directory = Directory.query.get(directory_id)
        if not directory:
            return send_404("no directory found")
        directoriesToDelete = deque([directory])
        layer = 0
        while directoriesToDelete:
            for _ in range(len(directoriesToDelete)):
                curr = directoriesToDelete.popleft()
                children = Directory.query.filter_by(parent_id=curr.id).all()
                if children and layer < 10:
                    directoriesToDelete.extend(children) 
                db.session.delete(curr)
            layer += 1
        db.session.commit()
        return send_200("directory deleted")
    except:
        return send_404("db failed")


@app.route("/get_file")
def get_file():
    username = request.args.get("username", None)
    window_id = request.args.get("windowId", None)
    file_name = request.args.get("fileName", None)
    file_id = request.args.get("fileId", None)
    file_s3_name = request.args.get("fileS3Name", None)
    if not (username and window_id and file_name and file_id and file_s3_name):
        return send_404("no credentials found")
    if file_id == -1 or file_s3_name == "dummyData":
        return send_200("dummy")
    try:
        if not validate_lp(username, window_id) and not dev_mode:
            return send_404("invalid session")
        res = s3_client.get_object(
            Bucket=config("AWS_BUCKET_NAME"),
            Key=file_s3_name,
        )
        content_type = res.get("ContentType", None)
        file_data = res.get("Body", None)
        print(content_type)
        if not (file_data and content_type):
            return send_404("incomplete file")
        return send_file(
            file_data,
            mimetype=content_type,
            # as_attachment=True,
            download_name=file_name
        )
    except:
        return send_404("db failed")


@app.route("/create_file", methods=["GET", "POST"])
def create_file():
    file = request.files.get("file", None)
    data = request.files.get("data", None)
    if not (file and data):
        return send_404("missing information")
    try:
        request_json = data.read().decode('utf-8')
        request_data = json.loads(request_json)
    except Exception as e:
        print(e)
        return send_404("could not decode request data")
    username = request_data.get("username", None)
    window_id = request_data.get("windowId", None)
    file_name = request_data.get("fileName", None)
    directory_id = request_data.get("directoryId", None)
    content_type = request_data.get("contentType", None)
    if not content_type:
        content_type = "text/plain"
    if not (username and window_id and file_name and directory_id and content_type):
        return send_404("no credentials found")
    try:
        if not validate_lp(username, window_id) and not dev_mode:
            return send_404("invalid session")
        if content_type == "text/plain":
            file_extension = file_name.split(".")[-1]
        else:
            file_extension = content_type.split("/")[-1]
        if not file_extension:
            return send_404("no file extension")
        duplicates = File.query.filter_by(directory_id=directory_id, name=file_name, content_type=content_type).all()
        if duplicates:
            toDelete = []
            for duplicate in duplicates:
                if not duplicate.s3_name:
                    toDelete.append(duplicate)
            for duplicate in toDelete:
                db.session.delete(duplicate)
            db.session.commit()
            duplicates = File.query.filter_by(directory_id=directory_id, name=file_name, content_type=content_type).all()
            if duplicates:
                file_name += f"({len(duplicates)})"
        new_file = File(directory_id=directory_id, name=file_name, content_type=content_type, username=username)
        db.session.add(new_file)
        db.session.commit()
        new_file = File.query.filter_by(directory_id=directory_id, name=file_name, content_type=content_type).first()
        new_file_id = new_file.id
        s3_name = f"file-{str(new_file_id)}.{file_extension}"
        new_file.s3_name = s3_name
        db.session.commit()
        s3_client.put_object(
            Bucket=config("AWS_BUCKET_NAME"),
            Key=s3_name,
            Body=file
        )
        new_file = File.query.get(new_file_id)
        if not new_file:
            return send_404("wtf")
        return jsonify(file_to_dict(new_file))
    except Exception as e:
        print(e)
        return send_404("db failed")


@app.route("/delete_file")
def delete_file():
    username = request.args.get("username", None)
    window_id = request.args.get("windowId", None)
    file_id = request.args.get("fileId", None)
    file_name = request.args.get("fileName", None)
    file_s3_name = request.args.get("fileS3Name", None)
    if not (username and window_id and file_id and file_name and file_s3_name):
        return send_404("no credentials found")
    try:
        if not validate_lp(username, window_id) and not dev_mode:
            return send_404("invalid session")
        file = File.query.get(file_id)
        if not file:
            return send_404("no file found")
        file_copy = file_to_dict(file)
        db.session.delete(file)
        db.session.commit()
        s3_client.delete_object(
            Bucket=config("AWS_BUCKET_NAME"),
            Key=file_s3_name
        )
        return jsonify(file_copy)
    except Exception as e:
        return send_404("db failed")


@app.route("/password_login")
def password_login():
    username = request.args.get("username", None)
    password = request.args.get("password", None)
    window_id = request.args.get("windowId", None)
    if not (username and password and window_id):
        return send_404("no credentials found")
    user = User.query.get(username)
    if not user:
        return send_404("user does not exist")
    if not bcrypt.checkpw(password.encode("utf8"), user.password.encode("utf8")):
        return send_404("password incorrect")
    try:
        lp = LoginProcess.query.get(username)
        if not lp:
            lp = LoginProcess(username=username, window_id=window_id)
            db.session.add(lp)
            db.session.commit()
        else:
            if lp.window_id != window_id:
                reset_lp(lp, window_id)
                db.session.commit()
        return send_200("password correct")
    except:
        return send_404("db failed")


@app.route("/twoFA_login")
def twoFA_login():
    username = request.args.get("username", None)
    code = request.args.get("code", None)
    window_id = request.args.get("windowId", None)
    if not (username and code and window_id):
        return send_404("no credentials found")
    twoFA = TwoFACode.query.get(username)
    if not twoFA:
        return send_404("no twoFA row found")
    lp = LoginProcess.query.get(username)
    if not lp:
        return send_404("no lp found")
    if (datetime.utcnow() - lp.date_created).total_seconds() > 600:
        reset_lp(lp, window_id)
        db.session.commit()
        return send_404("login session expired")
    if lp.twoFA_verified:
        return send_200("twoFA already verifyed")
    try:
        if compare_twoFA_code(twoFA.key, code):
            lp.twoFA_verified = True
            db.session.commit()
            return send_200("twoFA verified")
        else:
            return send_404("invalid twoFA code")
    except:
        return send_404("failed to compare twoFA code")


# @app.route("/fc_login", methods=["GET", "POST"])
# def fc_login():
#     try:
#         request_json = json.loads(request.data)
#         request_data = request_json["data"]
#     except:
#         return send_404("could not decode request data")
#     username = request_data.get("username", None)
#     picture_data_uri = request_data.get("pictureData", None)
#     window_id = request_data.get("windowId", None)
#     if not (username and picture_data_uri and window_id):
#         return send_404("no credentials found")
#     lp = LoginProcess.query.get(username)
#     if not lp:
#         return send_404("no lp found")
#     if (datetime.utcnow() - lp.date_created).total_seconds() > 600:
#         reset_lp(lp, window_id)
#         db.session.commit()
#         return send_404("login session expired")
#     if lp.fc_verified:
#         return send_200("fc already verifyed")
#     try:
#         input_filename = f"./fc_images/{username}_input.png"
#         reference_filename = f"./fc_images/{username}_reference.png"
#         _, encoded = picture_data_uri.split(",", 1)
#         input_data = b64decode(encoded)
#         with open(input_filename, "wb") as f:
#             f.write(input_data)
#         res = s3_client.get_object(
#             Bucket=config("AWS_BUCKET_NAME"),
#             Key=f"{username}_reference.png",
#         )

#         reference_data = res.get("Body", None)
#         if not reference_data:
#             return send_404("could not download reference")
#         with open(reference_filename, "wb") as f:
#             f.write(reference_data.read())

#         if compare_face_data(input_filename, reference_filename):
#             lp.fc_verified = True
#             db.session.commit()
#             remove_files([input_filename, reference_filename])
#             return send_200("fc verified")
#         else:
#             remove_files([input_filename, reference_filename])
#             return send_404("invalid fc")
#     except Exception as e:
#         print(e)
#         remove_files([input_filename, reference_filename])
#         return send_404("failed to verify fc")


@app.route("/validate_username")
def validate_username():
    username = request.args.get("username", None)
    if not username:
        return send_404("no username found")
    user = User.query.get(username)
    if user:
        return send_404("user already exists")
    else:
        return send_200("username usable")


@app.route("/send_verification")
def send_verification():
    username = request.args.get("username", None)
    if not username:
        return send_404("no username found")
    user = User.query.get(username)
    if user:
        return send_404("user already exists")
    code = "".join([str(random.randint(0, 9)) for _ in range(6)])
    vc = VerificationCode.query.get(username)
    if not vc:
        vc = VerificationCode(username=username, code=code)
        try:
            db.session.add(vc)
            db.session.commit()
        except Exception as e:
            print(e)
            return send_404("failed to create new vc row")
    else:
        vc.code = code
        try:
            db.session.commit()
        except Exception as e:
            print(e)
            return send_404("failed to updated exisitng vc row")
    try:
        send_verification_email(username, code)
    except Exception as e:
        print(e)
        return send_404("failed to send verification email")
    return send_200("verification email sent")


@app.route("/validate_verification")
def validate_verification():
    username = request.args.get("username", None)
    code = request.args.get("code", None)
    if not username or not code:
        return send_404("no credentials found")
    vc = VerificationCode.query.get(username)
    if not vc:
        return send_404("no valid vc found")
    if vc.code != code:
        print(f"correct code: ${vc.code}, input code: {code}")
        return send_404("invalid verification code")
    try:
        vc.verified = True
        db.session.commit()
        return send_200("email verified")
    except:
        return send_404("vc db failed")


@app.route("/send_twoFA_code")
def send_twoFA_code():
    username = request.args.get("username", None)
    if not username:
        return send_404("no username found")
    user = User.query.get(username)
    if user:
        return send_404("user already exists")
    try:
        twoFA = TwoFACode.query.get(username)
        if not twoFA:
            code_img, key = generate_twoFA_code(username)
            twoFA = TwoFACode(username=username, key=key)
            db.session.add(twoFA)
            db.session.commit()
        else:
            code_img, _ = generate_twoFA_code(username, twoFA.key)
        img_io = pil_img_to_io(code_img)
        return send_file(img_io, mimetype="image/png")
    except:
        return send_404("2FA code generation failed")


@app.route("/send_twoFA_code_refresh")
def send_twoFA_code_refresh():
    username = request.args.get("username", None)
    if not username:
        return send_404("no username found")
    return redirect(f"/send_twoFA_code?username={username}", 302)


@app.route("/validate_twoFA_code")
def validate_twoFA_code():
    username = request.args.get("username", None)
    code = request.args.get("code", None)
    if not username or not code:
        return send_404("no credentials found")
    twoFA = TwoFACode.query.get(username)
    if not twoFA:
        return send_404("no twoFA row found")
    try:
        if compare_twoFA_code(twoFA.key, code):
            twoFA.verified = True
            db.session.commit()
            return send_200("twoFA verified")
        else:
            return send_404("invalid twoFA code")
    except:
        return send_404("failed to compare twoFA code")


# @app.route("/face_recognition_setup", methods=["GET", "POST"])
# def face_recognition_setup():
#     try:
#         request_json = json.loads(request.data)
#         request_data = request_json["data"]
#     except:
#         return send_404("could not decode request data")
#     username = request_data.get("username", None)
#     picture_data_uri = request_data.get("pictureData", None)
#     if not username or not picture_data_uri:
#         return send_404("no credentials found")
#     user = User.query.get(username)
#     if user:
#         return send_404("user already exists")
#     try:
#         _, encoded = picture_data_uri.split(",", 1)
#         picture_data = b64decode(encoded)
#         # with open("yee.png", "wb") as f:
#         #     f.write(picture_data)
#         picture_data = BytesIO(picture_data)
#         picture_data.seek(0)
#         s3_client.put_object(
#             Bucket=config("AWS_BUCKET_NAME"),
#             Key=f"{username}_reference.png",
#             Body=picture_data
#         )
#         fc = FaceRecognition(username=username)
#         db.session.add(fc)
#         db.session.commit()
#         return send_200("picture uploaded")
#     except:
#         return send_404("failed to upload picture")


# @app.route("/validate_face_recognition", methods=["GET", "POST"])
# def validate_face_recognition():
#     try:
#         request_json = json.loads(request.data)
#         request_data = request_json["data"]
#     except:
#         return send_404("could not decode request data")
#     username = request_data.get("username", None)
#     picture_data_uri = request_data.get("pictureData", None)
#     if not username or not picture_data_uri:
#         return send_404("no credentials found")
#     try:
#         input_filename = f"./fc_images/{username}_input.png"
#         reference_filename = f"./fc_images/{username}_reference.png"
#         _, encoded = picture_data_uri.split(",", 1)
#         input_data = b64decode(encoded)
#         with open(input_filename, "wb") as f:
#             f.write(input_data)
#         res = s3_client.get_object(
#             Bucket=config("AWS_BUCKET_NAME"),
#             Key=f"{username}_reference.png",
#         )

#         reference_data = res.get("Body", None)
#         if not reference_data:
#             return send_404("could not download reference")
#         with open(reference_filename, "wb") as f:
#             f.write(reference_data.read())

#         if compare_face_data(input_filename, reference_filename):
#             fc = FaceRecognition.query.get(username)
#             if not fc:
#                 raise Exception("no fc found")
#             fc.verified = True
#             db.session.commit()
#             remove_files([input_filename, reference_filename])
#             return send_200("input matches reference")
#         else:
#             remove_files([input_filename, reference_filename])
#             return send_404("input does not match reference")
#     except Exception as e:
#         print(e)
#         remove_files([input_filename, reference_filename])
#         return send_404("failed to compare input and reference")


@app.route("/create_user")
def create_user():
    username = request.args.get("username", None)
    password = request.args.get("password", None)
    security_question = request.args.get("securityQuestion", None)
    security_answer = request.args.get("securityAnswer", None)
    if not (username and password and security_question and security_answer):
        return send_404("no credentials found")
    vc = VerificationCode.query.filter_by(username=username).one()
    if not vc:
        return send_404("no vc found")
    if not vc.verified:
        return send_404("vc not verified")
    twoFA = TwoFACode.query.filter_by(username=username).one()
    if not twoFA:
        return send_404("no twoFA found")
    if not twoFA.verified:
        return send_404("twoFA not verified")
    # fc = FaceRecognition.query.filter_by(username=username).one()
    # if not fc:
    #     return send_404("no fc found")
    # if not fc.verified:
    #     return send_404("fc not verified")
    user = User.query.filter_by(username=username).all()
    if user:
        return send_404("user already created")
    try:
        entry_directory = Directory(username=username, name="entry")
        db.session.add(entry_directory)
        db.session.commit()
        entry_directory = Directory.query.filter_by(username=username).all()

        if len(entry_directory) > 1:
            for directory in entry_directory[1:]:
                db.session.delete(directory)
            db.session.commit()

        entry_directory = entry_directory[0]
        secret = "S4_SECRET_" + pyotp.random_base32()
        user = User(username=username, password=password, security_question=security_question,
                    security_answer=security_answer, secret=secret, entry_directory=entry_directory.id)
        db.session.add(user)
        db.session.commit()
        data = '{"secret":"' + secret + '", "successMessage":"user created"}'
        return send_200("", data=data)
    except Exception as e:
        print(e)
        return send_404("failed to create user")


@app.route("/test_route")
def test_route():
    return send_200("server up")


# if __name__ == "__main__":
port = int(os.environ.get('PORT', 5000))
app.run(debug=True, port=port)
