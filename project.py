from flask import Flask, render_template, request, redirect,jsonify, url_for, flash
from flask import session as login_session
import random, string

app = Flask(__name__)

from sqlalchemy import create_engine, asc
from sqlalchemy.orm import sessionmaker
from database_setup import Base, Restaurant, MenuItem, User

from oauth2client.client import flow_from_clientsecrets, FlowExchangeError
import httplib2
import json
from flask import make_response
import requests

CLIENT_ID = json.loads(open('client_secret.json', 'r').read())['web']['client_id']

#Connect to Database and create database session
engine = create_engine('sqlite:///restaurantmenuwithusers.db?check_same_thread=False')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()


# User management
def createUser(login_session):
    user = User(
        name = login_session['username'],
        email = login_session['email'],
        picture = login_session['picture']
    )
    session.add(user)
    session.commit()
    return getUserId(login_session['email'])


def getUserInfo(user_id):
    return session.query(User).filter_by(id = user_id).one()


def getUserId(email):
    try:
        user = session.query(User).filter_by(email = email).one()
        return user.id
    except:
        return None


# Implement login functionality
@app.route('/gconnect', methods=['POST'])
def gconnect():
    # check client token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # convert the one time code into a credentials object
    code = request.data
    try:
        oauth_flow = flow_from_clientsecrets('client_secret.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(json.dumps('Failed to upgrade authorization code'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # check access token
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={}'.format(access_token))
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'
        return response

    # verfiy token is for the intended user
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(json.dumps('Token does not match given user ID'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # verify the application id is correct
    if result['issued_to'] != CLIENT_ID:
        response = make_response(json.dumps('Token Client ID does not match application'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # check to see if we are already logged on
    stored_access_token = login_session.get('access_token')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_access_token is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps('Current user is already connected'), 200)
        response.headers['Content-Type'] = 'application/json'
        return response

    # store the login information
    login_session['access_token'] = access_token
    login_session['provider_id'] = gplus_id

    # get user info
    userinfo_url = 'https://www.googleapis.com/oauth2/v1/userinfo'
    params = {'access_token': access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)
    data = json.loads(answer.text)

    login_session['provider'] = 'google'
    login_session['username'] = data['email'] # name isn't part of the payload
    login_session['picture'] = data['picture']
    login_session['email'] = data['email']

    # if the user is new add them
    user_id = getUserId(login_session['email'])
    if user_id == None:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    # display user information response
    output = '<h1>Welcome, {}!</h1>'.format(login_session['username'])
    output += '<img src="{}" style="width: 200px; height: 200px; border-radius: 150px"'.format(login_session['picture'])
    flash('You are now signed is as: {}'.format(login_session['username']))
    return output


@app.route('/disconnect')
def disconnect():
    # check if we have an active session
    access_token = login_session.get('access_token')
    if access_token is None:
        response = make_response(json.dumps('Current user is not logged in'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # disconnect based on the provider
    if login_session['provider'] == 'google':
        result = requests.post(
            'https://accounts.google.com/o/oauth2/revoke',
            params={'token': access_token},
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        status_code = getattr(result, 'status_code')
        print('Disconnect returned: {}'.format(status_code))
    elif login_session['provider'] == 'facebook':
        facebook_id = login_session['provider_id']
        url = 'https://graph.facebook.com/{}/permissions?access_toekn{}'.format(facebook_id, access_token)
        h = httplib2.Http()
        result = h.request(url, 'DELETE')[1]
        print('Disconnect result: {}'.format(result))

    # clear out the login_session no matter the response
    del login_session['provider']
    del login_session['access_token']
    del login_session['provider_id']
    del login_session['username']
    del login_session['picture']
    del login_session['email']
    del login_session['user_id']

    response = make_response(json.dumps('User disconnected'), 200)
    response.headers['Content-Type'] = 'application/json'
    return response


@app.route('/fbconnect', methods=['POST'])
def fbconnect():
    # check client token
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # BUGBUG - for some reason request.data would return b'xxx' formatted data
    # this was corrupting the url used in token exchange
    access_token = request.get_data(as_text=True)
    print('Access token: {}'.format(access_token))

    # exchange client token
    client_secrets = json.loads(open('fb_client_secret.json', 'r').read())
    app_id = client_secrets['web']['app_id']
    app_secret = client_secrets['web']['app_secret']
    url = 'https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id=%s&client_secret=%s&fb_exchange_token=%s' % (app_id, app_secret, access_token)
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]

    print(url)
    print('Token exchange result: {}'.format(result))
    # BUGBUG - the sample code had this as doing some funked out string parsing
    # token = result.split(',')[0].split(':')[1].replace('"', '')
    token = json.loads(result)['access_token']
    print('Token: {}'.format(token))

    # get profile data
    url = 'https://graph.facebook.com/v2.8/me?access_token={}&fields=name,id,email'.format(token)
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]

    print('me result: {}'.format(result))
    data = json.loads(result)
    login_session['provider'] = 'facebook'
    login_session['username'] = data['name']
    login_session['email'] = data['email']
    login_session['provider_id'] = data['id']
    login_session['access_token'] = token

    # get user image
    url = 'https://graph.facebook.com/v2.8/me/picture?access_token={}&redirect=0&height=200&width=200'.format(token)
    h = httplib2.Http()
    result = h.request(url, 'GET')[1]
    data = json.loads(result)
    print('me/picture result: {}'.format(result))

    login_session['picture'] = data['data']['url']

    # check if user exists
    user_id = getUserId(login_session['email'])
    if not user_id:
        user_id = createUser(login_session)
    login_session['user_id'] = user_id

    # display user information response
    output = '<h1>Welcome, {}!</h1>'.format(login_session['username'])
    output += '<img src="{}" style="width: 200px; height: 200px; border-radius: 150px"'.format(login_session['picture'])
    flash('You are now signed is as: {}'.format(login_session['username']))
    return output


@app.route('/login')
def showLogin():
    state = ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(32))
    login_session['state'] = state
    return render_template('login.html', STATE=state)


#JSON APIs to view Restaurant Information
@app.route('/restaurant/<int:restaurant_id>/menu/JSON')
def restaurantMenuJSON(restaurant_id):
    items = session.query(MenuItem).filter_by(restaurant_id = restaurant_id).all()
    return jsonify(MenuItems=[i.serialize for i in items])


@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/JSON')
def menuItemJSON(restaurant_id, menu_id):
    Menu_Item = session.query(MenuItem).filter_by(id = menu_id).one()
    return jsonify(Menu_Item = Menu_Item.serialize)

@app.route('/restaurant/JSON')
def restaurantsJSON():
    restaurants = session.query(Restaurant).all()
    return jsonify(restaurants= [r.serialize for r in restaurants])


#Show all restaurants
@app.route('/')
@app.route('/restaurant/')
def showRestaurants():
    restaurants = session.query(Restaurant).order_by(asc(Restaurant.name))
    if 'user_id' in login_session:
        return render_template('restaurants.html', restaurants = restaurants)
    else:
        return render_template('publicrestaurants.html', restaurants = restaurants)


#Create a new restaurant
@app.route('/restaurant/new/', methods=['GET','POST'])
def newRestaurant():
  if 'user_id' not in login_session:
      return redirect('/login')

  if request.method == 'POST':
      newRestaurant = Restaurant(
          name = request.form['name'],
          user_id = login_session['user_id']
      )
      session.add(newRestaurant)
      flash('New Restaurant %s Successfully Created' % newRestaurant.name)
      session.commit()
      return redirect(url_for('showRestaurants'))
  else:
      return render_template('newRestaurant.html')

#Edit a restaurant
@app.route('/restaurant/<int:restaurant_id>/edit/', methods = ['GET', 'POST'])
def editRestaurant(restaurant_id):
  if 'user_id' not in login_session:
      return redirect('/login')

  editedRestaurant = session.query(Restaurant).filter_by(id = restaurant_id).one()
  if request.method == 'POST':
      if request.form['name']:
        editedRestaurant.name = request.form['name']
        flash('Restaurant Successfully Edited %s' % editedRestaurant.name)
        return redirect(url_for('showRestaurants'))
  else:
    return render_template('editRestaurant.html', restaurant = editedRestaurant)


#Delete a restaurant
@app.route('/restaurant/<int:restaurant_id>/delete/', methods = ['GET','POST'])
def deleteRestaurant(restaurant_id):
  if 'user_id' not in login_session:
      return redirect('/login')

  restaurantToDelete = session.query(Restaurant).filter_by(id = restaurant_id).one()
  if request.method == 'POST':
    session.delete(restaurantToDelete)
    flash('%s Successfully Deleted' % restaurantToDelete.name)
    session.commit()
    return redirect(url_for('showRestaurants', restaurant_id = restaurant_id))
  else:
    return render_template('deleteRestaurant.html',restaurant = restaurantToDelete)

#Show a restaurant menu
@app.route('/restaurant/<int:restaurant_id>/')
@app.route('/restaurant/<int:restaurant_id>/menu/')
def showMenu(restaurant_id):
    restaurant = session.query(Restaurant).filter_by(id = restaurant_id).one()
    items = session.query(MenuItem).filter_by(restaurant_id = restaurant_id).all()
    if 'user_id' in login_session and login_session['user_id'] == restaurant.user_id:
        return render_template('menu.html', items = items, restaurant = restaurant)
    else:
        user = getUserInfo(restaurant.user_id)
        return render_template(
            'publicmenu.html',
            items = items,
            restaurant = restaurant,
            creator = user
        )


#Create a new menu item
@app.route('/restaurant/<int:restaurant_id>/menu/new/',methods=['GET','POST'])
def newMenuItem(restaurant_id):
  if 'user_id' not in login_session:
      return redirect('/login')

  restaurant = session.query(Restaurant).filter_by(id = restaurant_id).one()

  if login_session['user_id'] != restaurant.user_id:
      response = make_response(json.dumps('Accessing data you did not create'), 403)
      response.headers['Content-Type'] = 'application/json'
      return response

  if request.method == 'POST':
      newItem = MenuItem(
          name = request.form['name'],
          description = request.form['description'],
          price = request.form['price'],
          course = request.form['course'],
          restaurant_id = restaurant_id,
          user_id = login_session['user_id']
      )
      session.add(newItem)
      session.commit()
      flash('New Menu %s Item Successfully Created' % (newItem.name))
      return redirect(url_for('showMenu', restaurant_id = restaurant_id))
  else:
      return render_template('newmenuitem.html', restaurant_id = restaurant_id)

#Edit a menu item
@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/edit', methods=['GET','POST'])
def editMenuItem(restaurant_id, menu_id):
    if 'user_id' not in login_session:
        return redirect('/login')

    restaurant = session.query(Restaurant).filter_by(id = restaurant_id).one()
    if login_session['user_id'] != restaurant.user_id:
        response = make_response(json.dumps('Accessing data you did not create'), 403)
        response.headers['Content-Type'] = 'application/json'
        return response

    editedItem = session.query(MenuItem).filter_by(id = menu_id).one()

    if request.method == 'POST':
        if request.form['name']:
            editedItem.name = request.form['name']
        if request.form['description']:
            editedItem.description = request.form['description']
        if request.form['price']:
            editedItem.price = request.form['price']
        if request.form['course']:
            editedItem.course = request.form['course']
        session.add(editedItem)
        session.commit()
        flash('Menu Item Successfully Edited')
        return redirect(url_for('showMenu', restaurant_id = restaurant_id))
    else:
        return render_template('editmenuitem.html', restaurant_id = restaurant_id, menu_id = menu_id, item = editedItem)


#Delete a menu item
@app.route('/restaurant/<int:restaurant_id>/menu/<int:menu_id>/delete', methods = ['GET','POST'])
def deleteMenuItem(restaurant_id,menu_id):
    if 'user_id' not in login_session:
        return redirect('/login')

    restaurant = session.query(Restaurant).filter_by(id = restaurant_id).one()
    if login_session['user_id'] != restaurant.user_id:
        response = make_response(json.dumps('Accessing data you did not create'), 403)
        response.headers['Content-Type'] = 'application/json'
        return response

    itemToDelete = session.query(MenuItem).filter_by(id = menu_id).one()

    if request.method == 'POST':
        session.delete(itemToDelete)
        session.commit()
        flash('Menu Item Successfully Deleted')
        return redirect(url_for('showMenu', restaurant_id = restaurant_id))
    else:
        return render_template('deleteMenuItem.html', item = itemToDelete)




if __name__ == '__main__':
  app.secret_key = 'super_secret_key'
  app.debug = True
  app.run(host = '0.0.0.0', port = 5000)
