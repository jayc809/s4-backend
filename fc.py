# import face_recognition

# def compare_face_data(filename1, filename2):
#     img_1 = face_recognition.load_image_file(filename1)
#     img_1_encoding = face_recognition.face_encodings(img_1)[0]

#     img_2 = face_recognition.load_image_file(filename2)
#     img_2_encoding = face_recognition.face_encodings(img_2)[0]

#     results = face_recognition.compare_faces([img_1_encoding], img_2_encoding)
#     return results[0]
