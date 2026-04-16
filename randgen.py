import os
import codecs
from flask import Flask, request

app = Flask(__name__)

@app.route('/')
def index():
		rand_length = request.args.get('randlen')
		if rand_length == None:
				rand_length = 128
		else:
				rand_length = int(rand_length)
		return codecs.encode(os.urandom(rand_length), 'hex').decode()

if __name__ == '__main__':
		app.run()
