import torch
import torch.nn as nn
from torch.autograd import Variable
import torchvision
from torchvision import models

import argparse
import csv
import os
import numpy as np
import random
from patch_utils import*
from utils import*
from torchvision import datasets, models, transforms
from torch.utils.data import DataLoader 


parser = argparse.ArgumentParser()
parser.add_argument('--batch_size', type=int, default=1, help="batch size")
parser.add_argument('--num_workers', type=int, default=2, help="num_workers")
parser.add_argument('--train_size', type=int, default=2000, help="number of training images")
parser.add_argument('--test_size', type=int, default=2000, help="number of test images")
parser.add_argument('--noise_percentage', type=float, required=True, help="percentage of the patch size compared with the image size")
parser.add_argument('--probability_threshold', type=float, default=0.9, help="minimum target probability")
parser.add_argument('--lr', type=float, default=1.0, help="learning rate")
parser.add_argument('--max_iteration', type=int, default=1000, help="max iteration")
parser.add_argument('--target', type=int, default=859, help="target label")
parser.add_argument('--epochs', type=int, default=30, help="total epoch")
parser.add_argument('--data_dir', type=str, default='./imagenette/imagenette2-320', help="dir of the dataset")
parser.add_argument('--GPU', type=str, default='0', help="index pf used GPU")
parser.add_argument('--log_dir', type=str, default='patch_attack_log.csv', help='dir of the log')
parser.add_argument('--model', type=str, default='resnet18', help='model name')
parser.add_argument('--patch_type', type=str, default='square', help="patch type: rectangle or square")

parser.add_argument('--model_path', type=str, default='./imagenette/resnet18-imagenette.pt')


 

args = parser.parse_args()

# Patch attack via optimization
# According to reference [1], one image is attacked each time
# Assert: applied patch should be a numpy
# Return the final perturbated picture and the applied patch. Their types are both numpy
def patch_attack(image, applied_patch, mask, target, probability_threshold, model, lr=1, max_iteration=100):
    model.eval()
    applied_patch = torch.from_numpy(applied_patch)
    mask = torch.from_numpy(mask)
    target_probability, count = 0, 0
    perturbated_image = torch.mul(mask.type(torch.FloatTensor), applied_patch.type(torch.FloatTensor)) + torch.mul((1 - mask.type(torch.FloatTensor)), image.type(torch.FloatTensor))
    while target_probability < probability_threshold and count < max_iteration:
        count += 1
        # Optimize the patch
        perturbated_image = Variable(perturbated_image.data, requires_grad=True)
        per_image = perturbated_image
        per_image = per_image.cuda()
        output = model(per_image)
        target_log_softmax = torch.nn.functional.log_softmax(output, dim=1)[0][target]
        target_log_softmax.backward()
        patch_grad = perturbated_image.grad.clone().cpu()
        perturbated_image.grad.data.zero_()
        applied_patch = lr * patch_grad + applied_patch.type(torch.FloatTensor)
        applied_patch = torch.clamp(applied_patch, min=-3, max=3)
        # Test the patch
        perturbated_image = torch.mul(mask.type(torch.FloatTensor), applied_patch.type(torch.FloatTensor)) + torch.mul((1-mask.type(torch.FloatTensor)), image.type(torch.FloatTensor))
        perturbated_image = torch.clamp(perturbated_image, min=-3, max=3)
        perturbated_image = perturbated_image.cuda()
        output = model(perturbated_image)
        target_probability = torch.nn.functional.softmax(output, dim=1).data[0][target]

        #print("{}\t, target prob: {}".format(count, target_probability))


    perturbated_image = perturbated_image.cpu().numpy()
    applied_patch = applied_patch.cpu().numpy()
    return perturbated_image, applied_patch

os.environ["CUDA_VISIBLE_DEVICES"] = args.GPU

seed = 1
np.random.seed(seed)
random.seed(seed) 
torch.manual_seed(seed)
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")


# Load the model
# setting up model
model = models.resnet18(pretrained=True)
num_features = model.fc.in_features
model.fc = nn.Linear(num_features, 10) # multi-class classification (num_of_class == 307)
model.load_state_dict(torch.load(args.model_path)) 
model = model.to(device)
model.eval()

data_transforms = {
    'train': transforms.Compose([
        transforms.RandomResizedCrop(224),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ]),
    'val': transforms.Compose([
        transforms.Resize(size=(224,224)),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ]),
}


image_datasets = {x: datasets.ImageFolder(os.path.join(args.data_dir, x), data_transforms[x]) for x in ['train', 'val']}


train_loader = torch.utils.data.DataLoader(image_datasets['train'], batch_size=1, shuffle=False, num_workers=1)
test_loader = torch.utils.data.DataLoader(image_datasets['val'], batch_size=1, shuffle=False, num_workers=1)





# Test the accuracy of model on trainset and testset
trainset_acc, test_acc = test(model, train_loader), test(model, test_loader)
print('Accuracy of the model on clean trainset and testset is {:.3f}% and {:.3f}%'.format(100*trainset_acc, 100*test_acc))

# Initialize the patch
patch = patch_initialization(args.patch_type, image_size=(3, 224, 224), noise_percentage=args.noise_percentage)
print('The shape of the patch is', patch.shape)

#with open(args.log_dir, 'w') as f:
#    writer = csv.writer(f)
#    writer.writerow(["epoch", "train_success", "test_success"])

best_patch_epoch, best_patch_success_rate = 0, 0

 


# Generate the patch
for epoch in range(args.epochs):
    #print("=========== attack under {} epoch =========".format(epoch+1))
    cnt = 0
    train_total, train_actual_total, train_success = 0, 0, 0
    for (image, label) in train_loader:
        if( cnt >= args.train_size ):
            break

        train_total += 1
        print("\tepoch {} | target {} | {} image so far, total: {} | {} succeeded".format(epoch+1 , args.target, cnt, args.train_size, train_success), flush=True)

        #print("\t{} image so far, total: {}".format(cnt, args.train_size))
        assert image.shape[0] == 1, 'Only one picture should be loaded each time.'
        image = image.cuda()
        label = label.cuda()
        output = model(image)
        _, predicted = torch.max(output.data, 1)
        if predicted[0] == label and predicted[0].data.cpu().numpy() != args.target:
             train_actual_total += 1
             applied_patch, mask, x_location, y_location = mask_generation(args.patch_type, patch, image_size=(3, 224, 224))
             perturbated_image, applied_patch = patch_attack(image, applied_patch, mask, args.target, args.probability_threshold, model, args.lr, args.max_iteration)
             perturbated_image = torch.from_numpy(perturbated_image).cuda()
             output = model(perturbated_image)
             _, predicted = torch.max(output.data, 1)
             if predicted[0].data.cpu().numpy() == args.target:
                 train_success += 1
             patch = applied_patch[0][:, x_location:x_location + patch.shape[1], y_location:y_location + patch.shape[2]]

        cnt += 1
    mean, std = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]

    print("Epoch:{} Patch attack success rate on trainset: {:.3f}%".format(epoch+1, 100 * train_success / train_actual_total)) 
    test_success_rate = test_patch(args.patch_type, args.target, patch, test_loader, model)
    print("Epoch:{} Patch attack success rate on testset with universal patch: {:.3f}%".format(epoch+1, 100 * test_success_rate))

    # Record the statistics
    #with open(args.log_dir, 'a') as f:
    #    writer = csv.writer(f)
    #    writer.writerow([epoch, train_success_rate, test_success_rate])

    if test_success_rate > best_patch_success_rate:
        best_patch_success_rate = test_success_rate
        best_patch_epoch = epoch
        #np.save("best_patch.npy", torch.from_numpy(cur_patch))


        patch_name = "imagenette_{}_best_org_patch_{}_{}.npy".format(args.patch_type, str(args.noise_percentage).replace('.', ''), str(args.target))
        np.save(patch_name, torch.from_numpy(patch))



    # Load the statistics and generate the line
    #log_generation(args.log_dir)

print("The best patch is found at epoch {} with success rate {}% on testset".format(best_patch_epoch, 100 * best_patch_success_rate))







