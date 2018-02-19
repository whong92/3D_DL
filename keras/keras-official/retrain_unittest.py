import unittest
import retrain as rt

class TestKerasRetrain(unittest.TestCase):

    # assert that if i call fit on the model, the last layers change
    # (= are not the same as initial values)
    def test_training_last_layers(self):
        # TODO: Can be generalized: take an argument "number of top layers", include
        # them in a for loop and store weights in list

        train_data_dir = 'unit_test_images/'
        validation_data_dir = 'unit_test_images/'
        test_dir = 'unit_test_images/'

        model = rt.assemble_model()

        # store weights before
        before_softmax = model[:-1].get_weights()
        before_dense = model[:-2].get_weights()

        # train
        train_model(model)

        # store weights after
        after_softmax = model[:-1].get_weights()
        after_dense = model[:-2].get_weights()

        # check that something has changed
        self.assertTrue( (before_softmax != after_softmax).any() )
        self.assertTrue( (before_dense != after_dense).any() )

    # tests if the model stays stable for the layers we want it to be stable
    def test_base_model_stable(self):
        # dummy directories with black/white images
        train_data_dir = 'unit_test_images/'
        validation_data_dir = 'unit_test_images/'
        test_dir = 'unit_test_images/'

        # TODO delete
        count = 0

        # initialize two lists for weights
        before_transferred_weights = []
        after_transferred_weights = []

        # store all weights before
        for layer_bm, layer_fm in zip(base_model,model):
            before_transferred_weights.append(layer_fm.get_weights())
            count += 1

        # TODO delete
        print(count)

        # train
        base_model = InceptionV3(weights='imagenet', include_top=False)
        model = rt.assemble_model()
        train_model(model)

        # store all weights after
        for layer_bm, layer_fm in zip(base_model,model):
            after_transferred_weights.append(layer_fm.get_weights())

        # check that nothing changed for any of the layers
        for b, a in zip(before_transferred_weights,after_transferred_weights):
            self.assertTrue( (b == a).all() )


    # tests if model can be trained on two classes and perform significantly
    # better than random on a test set after 15 minutes of training
    def test_full_training(self):
        # dummy directories with black/white images
        train_data_dir = 'unit_test_images/'
        validation_data_dir = 'unit_test_images/'
        test_dir = 'unit_test_images/'

        # train
        model = rt.assemble_model()
        train_model(model)

        # evaluate
        # TODO: how many images does this generate
        score = evaluate(model)

        print("accuracy on b/w images")
        print(score[1])
        # check if significantly better than random
        self.assertTrue( score[1] > 0.7 )

    # TODO: write test that loss is never zero


if __name__ == '__main__':
    unittest.main()
