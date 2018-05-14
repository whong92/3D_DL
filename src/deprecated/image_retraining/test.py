from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
from datetime import datetime
import hashlib
import os.path
import random
import re
import sys
import tarfile

import numpy as np
import tensorflow as tf

from tensorflow.python.framework import graph_util
from tensorflow.python.framework import tensor_shape
from tensorflow.python.platform import gfile
from sklearn.manifold import TSNE

import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
from skimage import exposure, img_as_float, img_as_ubyte
import json
import re
import itertools
from sklearn.metrics import confusion_matrix
import matplotlib
import io
import pickle
from image_retraining.test_errors import *

FLAGS = None

def create_label_lists(label_path):
    """
    creates a label to encoding dict and a reverse dict via an output
    label txt file generated by retraining.py
    :param label_path: output label file name
    :param json_path: path to product json file
    :return: label to encoding dict and a reverse of that
    """
    with open(label_path) as f:
        labels = f.readlines()
    labels = [l.strip() for l in labels]

    label2idx = {}
    idx2label = {}
    for label_idx, label_name in enumerate(labels):
        label2idx[label_name] = label_idx
        idx2label[label_idx] = label_name

    return label2idx, idx2label

def get_test_files(filedir, label2idx, n=5):
    """
    Iterates through the folder structure and picks a random file from
    each folder. Goes through folder structure n times
    :param filedir: directory containing the folders/files
    :param label2idx: dict containing the encoding of each label
    :param n: number of times to iterate over folder structure
    :return: list of tuples of the form (label, encoding, filepath)
    """

    test_files = [] #list of (label, filename) tuple
    count_labels = 0

    for (dirpath, dirnames, filenames) in os.walk(filedir):
        if dirpath == filedir:
            continue
        num_files = len(filenames)
        if num_files > n:
            num_files = n

        # Extract the last dir name from dirpath
        last_dirname = os.path.basename(os.path.normpath(dirpath))
        new_dir = os.path.join(filedir, last_dirname)
        # Check if the directory has only one level below and not more than that
        if(dirpath != new_dir):
            raise InvalidDirectoryStructureError()

        for i in range(num_files):
            filepath = os.path.join(dirpath,filenames[i])
            label = os.path.basename(dirpath)
            test_files.append((label, label2idx[label], filepath))

    return test_files

def create_model_graph(model_info):
  """"Creates a graph from saved GraphDef file and returns a Graph object.

  Args:
    model_info: Dictionary containing information about the model architecture.

  Returns:
    Graph holding the trained Inception network, and the input tensor and result
    tensor as built in retraining.py
  """
  with tf.Graph().as_default() as graph:
    model_path = os.path.join(model_info['data_url'], model_info['model_file_name'])
    print('Model path: ', model_path)
    with gfile.FastGFile(model_path, 'rb') as f:
      graph_def = tf.GraphDef()
      graph_def.ParseFromString(f.read())
      resized_input_tensor, bottleneck_tensor, result_tensor = (tf.import_graph_def(
          graph_def,
          name='',
          return_elements=[
              model_info['resized_input_tensor_name'],
              model_info['bottleneck_tensor_name'],
              model_info['result_tensor_name'],
          ]))
  return graph, resized_input_tensor, bottleneck_tensor, result_tensor



def create_model_info(data_url):
  """Given the name of a model architecture, returns information about it.

  Args:
    Nothing

  Returns:
    Dictionary of information about the model, or None if the name isn't
    recognized

  Raises:
    ValueError: If architecture name is unknown.
  """
  model_file_name = 'output_graph.pb'
  result_tensor_name = 'final_result:0' #keras -- 'output_node0:0' # tflow -- 'final_result:0'
  resized_input_tensor_name = 'Mul:0' # keras -- 'input_1:0' # tflow -- 'Mul:0'
  input_width = 299
  input_height = 299
  input_depth = 3
  input_mean = 128
  input_std = 128
  bottleneck_tensor_name = 'pool_3/_reshape:0' # keras -- 'global_average_pooling2d_1/Mean:0' #tflow -- 'pool_3/_reshape:0'
  bottleneck_tensor_size = 2048
  return {
      'data_url': data_url,
      'result_tensor_name': result_tensor_name,
      'resized_input_tensor_name': resized_input_tensor_name,
      'model_file_name': model_file_name,
      'bottleneck_tensor_name' : bottleneck_tensor_name,
      'bottleneck_tensor_size': bottleneck_tensor_size,
      'input_width': input_width,
      'input_height': input_height,
      'input_depth': input_depth,
      'input_mean': input_mean,
      'input_std': input_std,
  }


def run_resize_data(sess, image_data, image_data_tensor, decoded_image_tensor, decoded_jpeg):
    # Decode the JPEG image, resize it, and rescale the pixel values.
    resized_input_values, decoded_jpeg_data \
        = sess.run([decoded_image_tensor, decoded_jpeg],
                   {image_data_tensor: image_data})
    return resized_input_values, decoded_jpeg_data

def adaptive_equalize(img):
    # Adaptive Equalization
    img = img_as_float(img)
    img_adapteq = exposure.equalize_adapthist(img, clip_limit=0.05)
    return img_as_ubyte(img_adapteq)

def tf_equalize(img_tnsr):
    IMAGE_WIDTH = 1280
    IMAGE_HEIGHT = 1024
    IMAGE_DEPTH = 3
    image_rot = tf.py_func(adaptive_equalize, [img_tnsr], tf.uint8)
    image_rot.set_shape([IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_DEPTH])  # when using pyfunc, need to do this??
    return image_rot

def add_jpeg_decoding(input_width, input_height, input_depth, input_mean,
                      input_std):
  """Adds operations that perform JPEG decoding and resizing to the graph..

  Args:
    input_width: Desired width of the image fed into the recognizer graph.
    input_height: Desired width of the image fed into the recognizer graph.
    input_depth: Desired channels of the image fed into the recognizer graph.
    input_mean: Pixel value that should be zero in the image for the graph.
    input_std: How much to divide the pixel values by before recognition.

  Returns:
    Tensors for the node to feed JPEG data into, and the output of the
      preprocessing steps.
  """
  if not check_nonnegative_args(input_width, input_height, input_depth, input_std):
    raise InvalidInputError('Input dimensions must be Nonnegative!')
  jpeg_data = tf.placeholder(tf.string, name='DecodeJPGInput')
  decoded_image = tf.image.decode_jpeg(jpeg_data, channels=input_depth)
  #decoded_image = tf_equalize(decoded_image)
  decoded_image_as_float = tf.cast(decoded_image, dtype=tf.float32)
  decoded_image_4d = tf.expand_dims(decoded_image_as_float, 0)
  resize_shape = tf.stack([input_height, input_width])
  resize_shape_as_int = tf.cast(resize_shape, dtype=tf.int32)
  resized_image = tf.image.resize_bilinear(decoded_image_4d,
                                           resize_shape_as_int)
  offset_image = tf.subtract(resized_image, input_mean)
  mul_image = tf.multiply(offset_image, 1.0 / input_std)
  return jpeg_data, mul_image, decoded_image


def eval_result(result_tensor, ground_truth, idx2label):

    if not check_confidence_tensor(result_tensor):
        raise InvalidInputError('Result confidence tensor invalid!')

    result = np.argmax(result_tensor,axis=1)
    prediction = (ground_truth==result[0])
    correct_label = idx2label[ground_truth]
    predicted_label = idx2label[result[0]]
    print('predicted: ', predicted_label, ' correct: ', correct_label)
    return prediction, correct_label, predicted_label


def extract_summary_tensors(test_results, label2idx):

    confidences = []
    predictions = []
    truth = []

    for result in test_results:
        confidences.extend(result['class_confidences'])# of shape [batch_size, n_class]
        predictions.append(label2idx[result['predicted_label']])
        truth.append(label2idx[result['correct_label']])

    confidences = np.array(confidences)
    predictions = np.array(predictions)
    truth = np.array(truth)
    return confidences, predictions, truth


def plot_confusion_matrix(cm, classes, normalize=False,
                          title='Confusion matrix',
                          cmap=plt.cm.Blues):
    """
    This function prints and plots the confusion matrix.
    Normalization can be applied by setting `normalize=True`.
    """
    if (not check_confusion_matrix(cm)):
        raise InvalidInputError('Confusion Matrix Invalid!')
    if not (len(classes) == cm.shape[0]):
        raise InvalidInputError('Number of classes incompatible with CM!')
    if normalize:
        cm = cm.astype('float') / cm.sum(axis=1)[:, np.newaxis]

    plt.imshow(cm, interpolation='nearest', cmap=cmap)
    plt.title(title)
    plt.colorbar()
    tick_marks = np.arange(len(classes))
    plt.xticks(tick_marks, classes, rotation=45)
    plt.yticks(tick_marks, classes)

    fmt = '.2f' if normalize else 'd'
    thresh = cm.max() / 2.
    for i, j in itertools.product(range(cm.shape[0]), range(cm.shape[1])):
        plt.text(j, i, format(cm[i, j], fmt),
                 horizontalalignment="center",
                 color="white" if cm[i, j] > thresh else "black")

    plt.tight_layout()
    plt.ylabel('True label')
    plt.xlabel('Predicted label')

    # convert to tf image
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    image = tf.image.decode_png(buf.getvalue(), channels=4)
    image = tf.expand_dims(image, 0)
    plt.clf()

    return image

def compute_sensitivity(cm):

    if (not check_confusion_matrix(cm)):
        raise InvalidInputError('Confusion Matrix Invalid!')

    cm = np.array(cm)
    relevant = np.sum(cm,axis=1)
    sensitivity = np.zeros(relevant.shape)
    for i in range(len(sensitivity)):
        if relevant[i] == 0:
            sensitivity[i] = -1
            continue
        sensitivity[i] = cm[i,i]/relevant[i]
    return sensitivity

def compute_precision(cm):

    if (not check_confusion_matrix(cm)):
        raise InvalidInputError('Confusion Matrix Invalid!')

    cm = np.array(cm)
    relevant = np.sum(cm,axis=0)
    precision = np.zeros(relevant.shape)
    for i in range(len(precision)):
        if relevant[i] == 0:
            precision[i] = -1
            continue
        precision[i] = cm[i,i]/relevant[i]
    return precision

def plot_bar(x,heights, heights2=None, title='Bar Chart', xlabel='X', ylabel='Y'):
    bar_width = 0.4
    x = np.array(x)
    plt.bar(x,heights,bar_width)
    if heights2 is not None:
        plt.bar(x-bar_width,heights2,bar_width)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)

    plt.tight_layout()

    # convert to tf image
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    image = tf.image.decode_png(buf.getvalue(), channels=4)
    image = tf.expand_dims(image, 0)
    plt.clf()

    return image


def summarize_results(sess, label2idx, per_class_test_results, print_results=False):
    # Check if directory already exists. If so, create a new one
    if tf.gfile.Exists(FLAGS.model_source_dir + '/test_results'):
        tf.gfile.DeleteRecursively(FLAGS.model_source_dir + '/test_results')
    tf.gfile.MakeDirs(FLAGS.model_source_dir + '/test_results')

    # create the summary setup
    summary_writer = tf.summary.FileWriter(FLAGS.model_source_dir + '/test_results', sess.graph)

    c = len(label2idx.keys())

    predictions = []
    truth = []
    for label in per_class_test_results:
        test_results = per_class_test_results[label]
        n = len(test_results)
        confidences, class_predictions, class_truth = extract_summary_tensors(test_results, label2idx)
        predictions.extend(class_predictions)
        truth.extend(class_truth)

        confidences_tensor = tf.placeholder(tf.float32, shape=(n,))
        confidences_summary_buffer = tf.summary.histogram('Confidences_' + label, confidences_tensor)

        # Summarize confidences in a multi-tiered histogram
        for i in range(c):
            confidences_summary = sess.run(confidences_summary_buffer, feed_dict={confidences_tensor: confidences[:,i]})
            summary_writer.add_summary(confidences_summary,i)

    # Confusion Matrix Plot

    cm = confusion_matrix(truth, predictions)

    cm_img = plot_confusion_matrix(cm, classes=label2idx.keys())
    summary_op = tf.summary.image("Confusion_Matrix", cm_img)
    confusion_summary = sess.run(summary_op)
    summary_writer.add_summary(confusion_summary)

    sensitivity = compute_sensitivity(cm)
    precision = compute_precision(cm)

    prec_img = plot_bar(range(c), precision,  sensitivity  , title='Class Precision', xlabel='Class', ylabel='Precision and Sensitivity')
    summary_op = tf.summary.image("Precision", prec_img)
    prec_summary = sess.run(summary_op)
    summary_writer.add_summary(prec_summary)
    if print_results:
        print('Confusion Matrix: ', cm)
        print('Sensitivity: ', sensitivity)
        print('Precision: ',precision)

    summary_writer.close()


def main(_):
    # Needed to make sure the logging output is visible.
    # See https://github.com/tensorflow/tensorflow/issues/3047
    tf.logging.set_verbosity(tf.logging.INFO)

    # Gather information about the model architecture we'll be using.
    model_info = create_model_info(FLAGS.model_source_dir)

    graph, resized_input_tensor, bottleneck_tensor, result_tensor = create_model_graph(model_info)

    # Look at the folder structure, and create lists of all the images.
    label2idx, idx2label = create_label_lists(FLAGS.label_path)
    test_data = get_test_files(FLAGS.test_file_dir, label2idx, n=FLAGS.num_test)

    with tf.Session(graph=graph) as sess:
        if FLAGS.test_result_file is None:

            # set up jpeg decoding network
            jpeg_data_tensor, resized_image_tensor, decoded_jpeg_tensor = add_jpeg_decoding(
                model_info['input_width'], model_info['input_height'],
                model_info['input_depth'], model_info['input_mean'],
                model_info['input_std'])

            # Set up all our weights to their initial default values.
            init = tf.global_variables_initializer()
            sess.run(init)

            per_class_test_results = {}
            for label in label2idx:
                per_class_test_results[label] = []
                features = []

                count = 0

            for test_datum in test_data:
                if(count%FLAGS.notify_interval == 0):
                    print('processed {0}, {1} more to go'.format(count,len(test_data)-count) )

                test_result = {}

                # read in image data
                image_data = gfile.FastGFile(test_datum[2], 'rb').read()
                ground_truth = test_datum[1]

                # fetch resized image from the resizing network
                resized_image_data, decoded_jpeg_data = run_resize_data(
                    sess, image_data, jpeg_data_tensor, resized_image_tensor, decoded_jpeg_tensor)

                # feed resized image into Inception network, output result
                result, bottleneck = sess.run(
                    [result_tensor, bottleneck_tensor],
                    feed_dict={resized_input_tensor: resized_image_data}
                )

                # decode result tensor here since we don't have access to the prediction tensor
                test_result['prediction'], test_result['correct_label'], test_result['predicted_label'] = \
                    eval_result(result, ground_truth, idx2label)
                test_result['class_confidences'] = result
                test_result['features'] = bottleneck[0]
                per_class_test_results[test_result['correct_label']].append(test_result)
                features.append(bottleneck[0])

                count += 1
        else:
            print('Pre supplied test result file found, loading ... ')
            pickled_test_result = open(FLAGS.test_result_file,'rb')
            per_class_test_results = pickle.load(pickled_test_result)

        summarize_results(sess ,label2idx, per_class_test_results, print=True)
    #
    # features = np.array(features)
    # print('feature shape: ', features.shape)
    #
    # print('Performing dimensionality reduction with tSNE...')
    #
    # # dim reduction on the features!
    # tsne = TSNE(perplexity=30, n_components=2, init='pca', n_iter=5000, method='exact')
    # two_d_embeddings = tsne.fit_transform(features)
    #
    # from textwrap import wrap
    # # Visualize some data
    # num_correct = 0
    # num_incorrect = 0
    # num_viz = 4
    # plt.figure()
    # for result in test_results:
    #     predicted = result['predicted_label']
    #     correct = result['correct_label']
    #     if result['prediction']:
    #         num_correct += 1
    #         if(num_correct > num_viz):
    #             continue
    #         plt.subplot(2,num_viz,num_correct)
    #         plt.imshow(result['image'])
    #         plt.title("\n".join(wrap('Pred: {0}'.format(label2name[correct]),30)))
    #     if not result['prediction']:
    #         num_incorrect += 1
    #         if(num_incorrect > num_viz):
    #             continue
    #         plt.subplot(2, num_viz, num_incorrect + num_viz)
    #         plt.imshow(result['image'])
    #         plt.title("\n".join(wrap('Pred: {0}, \n Corr: {1}'.format(label2name[predicted], label2name[correct]),30)))
    # print('accuracy : {}%'.format(100*num_correct/len(test_results)))
    # plt.show()
    #
    # label2col = {}
    # for label in label2name.keys():
    #     label_id = int(label)
    #     label2col[label] = '#' + "{0:0{1}x}".format((label_id + np.random.randint(0,0xFFFFFF)) % 0xFFFFFF,6)
    #     print(label + ' color is : ' + label2col[label])
    #
    # plt.figure()
    # for i, result in enumerate(test_results):
    #     x,y = two_d_embeddings[i,:]
    #     plt.scatter(x,y,color=label2col[result['correct_label']], label=result['correct_label'])
    # plt.title('2D visualization of features via TSNE reduction')

    with open(FLAGS.test_result_path, 'wb') as f:  # Python 3: open(..., 'wb')
        pickle.dump(per_class_test_results, f)



if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument(
      '--model_source_dir',
      type=str,
      default='./tmp/',
      help="""\
      directory containing the model graph.\
      """)

  parser.add_argument(
      '--label_path',
      type=str,
      default='./tmp/output_labels.txt',
      help="""\
          file containing the labels associated with the products.\
          """)

  parser.add_argument(
      '--test_file_dir',
      type=str,
      default='D:/PycharmProjects/Products2',
      help="""\
              directory containing the test images.\
              """)

  parser.add_argument(
      '--test_result_path',
      type=str,
      default='./tmp/training_results.pkl',
      help="""\
              directory to store the test results.\
              """)

  parser.add_argument(
      '--test_result_file',
      type=str,
      default=None,
      help="""\
              directory to store the test results.\
              """)

  parser.add_argument(
      '--num_test',
      type=int,
      default=50,
      help="""\
                number of samples per class to test.\
                """)

  parser.add_argument(
      '--notify_interval',
      type=int,
      default=20,
      help="""\
                    number of classes to test.\
                    """)

  FLAGS, unparsed = parser.parse_known_args()

  tf.app.run(main=main)